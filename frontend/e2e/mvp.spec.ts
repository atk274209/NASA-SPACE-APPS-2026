import { expect, test, type Page } from '@playwright/test'

// Every page must load without console errors, and the console must never contact a host
// other than the AM server itself (offline-first: fonts and scripts are bundled).
const problems: string[] = []
test.beforeEach(async ({ page, baseURL }) => {
  problems.length = 0
  const origin = new URL(baseURL!).origin
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (!['http:', 'https:'].includes(u.protocol)) return
    if (u.origin !== origin) problems.push(`external request: ${r.url()}`)
  })
})
test.afterEach(() => {
  expect(problems, problems.join('\n')).toEqual([])
})

async function chooseCrew(page: Page, code: string) {
  await page.goto('/')
  const picker = page.getByLabel('Crew member')
  await expect(picker).toBeVisible()
  const label = await picker.locator('option', { hasText: `(${code})` }).first().textContent()
  await picker.selectOption({ label: label!.trim() })
}

test('overview shows the simulated crew member, body map and guidance', async ({ page }) => {
  await chooseCrew(page, 'SIM-ALPHA')
  await expect(page.getByRole('heading', { name: 'Simulated astronaut Alpha' })).toBeVisible()
  await expect(page.getByText('Simulated', { exact: true }).first()).toBeVisible()
  const systems = page.getByRole('list', { name: 'Body systems' })
  await expect(systems).toBeVisible()
  await systems.getByRole('button').first().click()
  await expect(page.getByText(/DEMO/).first()).toBeVisible()
})

test('a measurement can be recorded, including a converted unit', async ({ page }) => {
  await chooseCrew(page, 'SIM-CHARLIE')
  await page.goto('/record')
  await expect(page.getByRole('heading', { name: 'Record a measurement' })).toBeVisible()
  await page.getByLabel('Indicator', { exact: true }).selectOption('body_temperature')
  await page.getByLabel('Unit', { exact: true }).selectOption('degF') // entered in Fahrenheit, stored in Celsius
  await page.getByLabel('Value', { exact: true }).fill('98.6')
  await page.getByRole('button', { name: 'Record measurement' }).click()
  await expect(page.getByText('Accepted', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Stored as', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Assessment' })).toBeVisible()
})

test('constipation mentioned in chat becomes personalised missions after confirmation', async ({ page }) => {
  await chooseCrew(page, 'SIM-CHARLIE')
  await page.goto('/companion')
  await page.getByRole('button', { name: /New conversation|Start a conversation/ }).first().click()
  const box = page.getByLabel('Message', { exact: true })
  await box.fill('Kabızlık sorunum var, iki gündür tuvalete çıkamadım.')
  await box.press('Enter')
  // the model is offline: AM answers with its rule-based summary and still notices the symptom
  await expect(page.getByText(/AI conversation is unavailable/).first()).toBeVisible({ timeout: 30_000 })
  const confirm = page.getByRole('button', { name: 'Confirm symptom' }).first()
  await expect(confirm).toBeVisible()
  // the proposal block that owns this button: symptom label, quoted sentence, severity picker
  const proposal = confirm.locator('xpath=ancestor::div[.//input[@type="radio"]][1]')
  await expect(proposal.getByText('Constipation', { exact: true })).toBeVisible()
  await expect(proposal.getByText(/Kabızlık sorunum var/)).toBeVisible()
  await expect(confirm).toBeDisabled() // nothing is recorded until the astronaut rates and confirms it
  await proposal.locator('input[type="radio"][value="moderate"]').check({ force: true })
  await confirm.click()
  await expect(page.getByRole('button', { name: 'Confirm symptom' })).toHaveCount(0)

  await page.goto('/missions')
  await expect(page.getByRole('heading', { name: 'Daily missions' })).toBeVisible()
  await expect(page.getByText('Log bowel movements today').first()).toBeVisible()
  await expect(page.getByText(/constipation/i).first()).toBeVisible()
})

test('streaks page shows adherence streaks', async ({ page }) => {
  await chooseCrew(page, 'SIM-ALPHA')
  await page.goto('/streaks')
  await expect(page.getByRole('heading', { name: 'Streaks' })).toBeVisible()
  await expect(page.getByText('Longest').first()).toBeVisible()
})

test('journal marks a severe-symptom day red and writes a labelled summary offline', async ({ page }) => {
  await chooseCrew(page, 'SIM-ALPHA')
  await page.goto('/journal')
  await expect(page.getByRole('heading', { name: 'Mission journal' })).toBeVisible()
  const days = page.getByRole('navigation', { name: 'Mission days' })
  await days.getByRole('button', { name: /Day 27\b/ }).click()
  await expect(page.getByRole('heading', { name: /Mission day 0?27/ })).toBeVisible()
  await expect(page.getByText(/Red/).first()).toBeVisible()
  await page.getByRole('button', { name: /Write AI summary/ }).click()
  // offline: the summary is the labelled rule-based template, never presented as model-written
  await expect(page.getByText(/Rule-based template from this day's records/).first()).toBeVisible({ timeout: 30_000 })
})

test('alerts show "is this serious?" guidance', async ({ page }) => {
  await chooseCrew(page, 'SIM-BRAVO')
  await page.goto('/alerts')
  await expect(page.getByRole('heading', { name: 'Alert center' })).toBeVisible()
  await expect(page.getByText('Is this serious?').first()).toBeVisible()
})

test('science analysis and settings diagnostics work offline', async ({ page }) => {
  await chooseCrew(page, 'SIM-ALPHA')
  await page.goto('/science')
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/patterns?/).first()).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByText(/Diagnostics/).first()).toBeVisible()
})

test('pages fit a 375 px phone screen without sideways scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await chooseCrew(page, 'SIM-ALPHA')
  const pages = ['/', '/record', '/companion', '/health', '/baselines', '/alerts', '/missions', '/streaks',
    '/science', '/science?tab=observations', '/science?tab=series', '/science?tab=library', '/journal',
    '/settings', '/system']
  for (const path of pages) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow, `${path} overflows by ${overflow}px`).toBeLessThanOrEqual(1)
  }
})
