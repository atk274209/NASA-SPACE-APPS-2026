import { expect, test, type Page } from '@playwright/test'

// End-to-end tests of the signed-in, role-based app. The local language model is deliberately
// unreachable (see playwright.config.ts): this doubles as the offline-operation test, because
// every safety path, the doctor feed and cabin control must work without it.

// Every page must load without console errors, and the app must never contact a host other than
// the AM server itself (offline-first: fonts and scripts are bundled).
const problems: string[] = []
test.beforeEach(async ({ page, baseURL }) => {
  problems.length = 0
  const origin = new URL(baseURL!).origin
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`))
  page.on('console', (m) => {
    // a wrong password is answered with HTTP 401 by design; the browser logs that as a console error
    if (m.type() === 'error' && !m.text().includes('401')) problems.push(`console: ${m.text()}`)
  })
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (!['http:', 'https:'].includes(u.protocol)) return
    if (u.origin !== origin) problems.push(`external request: ${r.url()}`)
  })
})
test.afterEach(() => {
  expect(problems, problems.join('\n')).toEqual([])
})

async function signIn(page: Page, username: string) {
  await page.context().clearCookies()
  await page.goto('/')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(`${username}-test`)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
}

async function say(page: Page, text: string) {
  const conversations = page.getByRole('button', { name: 'New conversation' })
  await conversations.click()
  const box = page.getByLabel('Message', { exact: true })
  await box.fill(text)
  await box.press('Enter')
}

test('sign-in refuses a wrong password and each role gets its own simple menu', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('ast001')
  await page.getByLabel('Password').fill('wrong-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText('Wrong username or password.')).toBeVisible()

  await signIn(page, 'ast001')
  const astronautNav = page.getByRole('navigation', { name: 'Primary' }).getByRole('link')
  await expect(astronautNav).toHaveText(['Companion', 'Intercom', 'Diary', 'Health', 'Journal'])

  await signIn(page, 'doctor')
  const doctorNav = page.getByRole('navigation', { name: 'Primary' }).getByRole('link')
  await expect(doctorNav).toHaveText(['Crew', 'Intercom', 'AM Core', 'Environment', 'Alerts', 'Settings'])
})

test('an urgent symptom reaches the doctor at once and waits for approval', async ({ page }) => {
  await signIn(page, 'ast001')
  await say(page, 'Göğsümde sıkışma var')
  // fixed safety words, in Turkish, without the language model
  await expect(page.getByText(/ekip sağlık görevlisine veya uçuş cerrahına şimdi ulaş/).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Sent to the doctor: waiting for approval')).toBeVisible()

  await signIn(page, 'doctor')
  await expect(page.getByRole('heading', { name: /Awaiting doctor approval/ })).toBeVisible()
  const card = page.getByRole('listitem').filter({ hasText: 'Göğsümde sıkışma var' }).first()
  await expect(card.getByText('Red', { exact: true }).first()).toBeVisible()
  await card.getByPlaceholder(/Your decision or advice/).fill('Call me on the private loop now.')
  await card.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByText(/Approved by doctor/).first()).toBeVisible()

  await signIn(page, 'ast001')
  await expect(page.getByText('From your doctor')).toBeVisible()
  await expect(page.getByText('Call me on the private loop now.')).toBeVisible()
})

test('a value said in conversation is recorded with one tap', async ({ page }) => {
  await signIn(page, 'ast002')
  await say(page, 'Dün gece 6 saat uyudum')
  const value = page.getByRole('listitem').filter({ hasText: 'Sleep duration' })
  await expect(value).toBeVisible({ timeout: 30_000 })
  await value.getByRole('button', { name: 'Record' }).click()
  await expect(value).toHaveCount(0)
})

test('a comfort complaint adjusts the quarters and the doctor sees why', async ({ page }) => {
  await signIn(page, 'ast003')
  await say(page, "I'm cold tonight")
  await expect(page.getByText(/It felt cold, so I raised your quarters from 22 to 23/).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/AM changed temperature 22 → 23/)).toBeVisible()

  await signIn(page, 'doctor')
  await page.getByRole('link', { name: 'Environment' }).click()
  await expect(page.getByText('SIMULATED DEMONSTRATION LIMITS').first()).toBeVisible()
  await expect(page.getByText(/Signal: “I'm cold tonight”/)).toBeVisible()
})

test('water is logged with one tap and the crew board ranks adherence', async ({ page }) => {
  await signIn(page, 'ast005')
  await page.getByRole('button', { name: /\+250 mL/ }).click()
  await expect(page.getByText(/0\.25\s*\/ 2\.5 L/)).toBeVisible()
  await page.getByRole('button', { name: /Vitamin D/ }).click()
  await expect(page.getByRole('button', { name: /✓ Vitamin D/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /Crew hydration board/ })).toBeVisible()
  await expect(page.getByText('earns nothing extra', { exact: false })).toBeVisible()
})

test('AM Core is sealed, shows visible decisions, and opens only with a reason', async ({ page }) => {
  await signIn(page, 'doctor')
  const run = await page.request.post('/api/test/end-day', { data: {} })
  expect(run.ok()).toBeTruthy()
  await page.getByRole('link', { name: 'AM Core' }).click()
  await expect(page.getByText('SEALED', { exact: true })).toBeVisible()
  await expect(page.getByText(/Attention for .* tomorrow/).first()).toBeVisible()
  const breakSeal = page.getByRole('button', { name: 'Break the seal for 2 hours' })
  await expect(breakSeal).toBeDisabled()
  await page.getByPlaceholder(/Emergency reason/).fill('E2E: crew member not answering the loop')
  await breakSeal.click()
  await expect(page.getByText(/OPEN · emergency/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Crew report' })).toBeVisible()

  await signIn(page, 'ast005')
  await expect(page.getByText('AM Core was opened')).toBeVisible()
})

test('crew analytics bento shows live cards from stored records', async ({ page }) => {
  await signIn(page, 'ast004')
  await page.getByRole('button', { name: /\+500 mL/ }).click()
  await signIn(page, 'doctor')
  const water = page.getByRole('region', { name: 'Crew water today' })
  await expect(water).toBeVisible()
  await expect(water.getByText(/500 mL/).first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Written to AM today' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Reported problems by body system' })).toBeVisible()
  const radar = page.getByRole('region', { name: /Wellbeing and adherence/ })
  await radar.getByRole('tab', { name: 'AST-004' }).click()
  await expect(radar.getByRole('img', { name: /Samuel Idowu/ })).toBeVisible()
  await expect(radar.getByText(/Water/).first()).toBeVisible()
})

test('phone width: no sideways scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  for (const user of ['ast004', 'doctor']) {
    await signIn(page, user)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, `${user}: page is wider than the screen`).toBeLessThanOrEqual(1)
  }
})

test('the private diary talks about the day and stays away from the doctor', async ({ page }) => {
  await signIn(page, 'ast002')
  await page.getByRole('link', { name: 'Diary' }).click()
  await expect(page.getByRole('heading', { name: 'Diary', level: 1 })).toBeVisible()
  const box = page.getByLabel('Write in your diary')
  await box.fill('Bugün robot kolla çalıştım, biraz yoruldum ama güzeldi.')
  await box.press('Enter')
  await expect(page.getByText('Bugün robot kolla çalıştım, biraz yoruldum ama güzeldi.')).toBeVisible()
  await expect(page.getByText('A good day').or(page.getByText('A difficult day'))).toBeVisible({ timeout: 30_000 })

  await signIn(page, 'doctor')
  await expect(page.getByText('Bugün robot kolla çalıştım')).toHaveCount(0)
  const sealed = await page.request.get('/api/v1/doctor/diary/2')
  expect(sealed.status()).toBe(423)
})

test('a simulated space event shows what happened and how AM reacted', async ({ page }) => {
  await signIn(page, 'doctor')
  const ev = await page.request.post('/api/v1/demo/events', { data: { kind: 'quarters_heat' } })
  expect(ev.ok()).toBeTruthy()
  const card = page.getByRole('status', { name: /Simulated event: Quarters overheating/ })
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.getByText(/raised a (warning|critical) alert/)).toBeVisible()
  await expect(card.getByText(/AM paused its own temperature control/)).toBeVisible()
  await card.getByRole('button', { name: 'Dismiss' }).click()
  await expect(card).toHaveCount(0)
})

test('AM shares out the day by energy and morale', async ({ page }) => {
  await signIn(page, 'doctor')
  await page.request.post('/api/test/checkins', { data: { astronaut_id: 4, ratings: { mood: 1, motivation: 1, stress: 5, fatigue: 5, sleep_quality: 1 } } })
  await page.request.post('/api/v1/duties/today/replan')
  await page.reload()
  await expect(page.getByRole('heading', { name: "Today's duty roster" })).toBeVisible()
  await signIn(page, 'ast004')
  const card = page.getByRole('region', { name: "Today's duties" })
  await expect(card.getByText('Light day', { exact: true })).toBeVisible()
  await expect(card.getByText('Private call home')).toBeVisible()
  await card.getByRole('checkbox').first().click()
  await expect(card.getByText(/1 of \d+ done/)).toBeVisible()
})

test('the body map stands in the crew quarters in 3D, with the flat map one tap away', async ({ page }) => {
  await signIn(page, 'ast001')
  await page.getByRole('link', { name: 'Health' }).click()
  const toggle = page.getByRole('group', { name: 'Body map view' })
  if (await toggle.count()) {
    await expect(page.getByRole('img', { name: /Body map in 3D inside the crew quarters/ })).toBeVisible({ timeout: 20_000 })
    await toggle.getByRole('button', { name: 'Flat view' }).click()
    await expect(page.getByRole('radiogroup', { name: /Body map/ })).toBeVisible()
    await toggle.getByRole('button', { name: '3D view' }).click()
    await expect(page.getByRole('img', { name: /Body map in 3D/ })).toBeVisible()
  } else {
    // no WebGL in this browser: the flat map is shown instead
    await expect(page.getByRole('radiogroup', { name: /Body map/ })).toBeVisible()
  }
  await page.getByRole('button', { name: /Heart and circulation/ }).click()
  await expect(page.getByRole('heading', { name: /Heart and circulation/ }).first()).toBeVisible()
})

test('the flight surgeon and an astronaut talk on their private intercom loop', async ({ page }) => {
  await signIn(page, 'doctor')
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Intercom' }).click()
  await page.getByRole('navigation', { name: 'Intercom loops' }).getByRole('button', { name: /Samuel Idowu/ }).click()
  await page.getByLabel('Message on this loop').fill('Please call me after the EVA prep.')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Please call me after the EVA prep.')).toBeVisible()

  await signIn(page, 'ast004')
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Intercom' }).click()
  await expect(page.getByText('Please call me after the EVA prep.')).toBeVisible()
  await page.getByLabel('Message on this loop').fill('Copy, calling at 15:00.')
  await page.getByLabel('Message on this loop').press('Enter')
  await expect(page.getByText('Copy, calling at 15:00.')).toBeVisible()
})
