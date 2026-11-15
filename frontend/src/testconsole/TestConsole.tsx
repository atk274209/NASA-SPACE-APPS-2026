// AMC Test Console (TEST ONLY). Live test entries, End Day and scenario fixtures.
// Every record written here is SIMULATED test data, tagged MANUAL_TEST_ENTRY or SIMULATED_GENERATOR.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { ErrorNote, Section } from '../components/ui'
import { tc, type CabinControl, type CrewDay, type EndDayRun, type TestState, type User } from './api'

const STEP_LABELS: Record<string, string> = {
  hydration: 'Daily water total and points',
  check_logs: 'Check daily logs',
  lock_day: 'Lock the closed day',
  baselines: 'Update personal baselines',
  anomaly_persistence: 'Anomaly and persistence check',
  correlation: 'Historical correlation',
  journal: 'Daily journal',
  am_core: 'AMC Core: sealed reports and decisions',
  advance_clock: 'Advance mission clock',
  missions: "Plan next day's missions",
  streaks: 'Update streaks',
  exams_points: 'Routine exams and points',
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

function TestBanner() {
  return (
    <div
      role="note"
      className="sticky top-0 z-20 border-b-2 border-sev-watch bg-[repeating-linear-gradient(135deg,#3a2f0b_0_12px,#2a2208_12px_24px)] px-4 py-2 text-center text-sm font-semibold tracking-wide text-sev-watch"
    >
      TEST CONSOLE · SIMULATED TEST DATA ONLY · NOT THE AMC MAIN APP
    </div>
  )
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="text-ink-dim">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-faint">{hint}</span>}
    </label>
  )
}

const input = 'h-9 rounded-md border border-seam-strong bg-deck px-2 text-ink outline-none focus:border-signal'
const button =
  'h-9 rounded-md bg-ink px-4 font-semibold text-void hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50'

// ------------------------------------------------------------------ sign-in
function SignIn({ fileHint }: { fileHint: string | null }) {
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: () => tc.login(username, password),
    onSuccess: () => qc.invalidateQueries(),
  })
  return (
    <main className="mx-auto grid max-w-sm gap-4 px-4 py-12">
      <h1 className="text-2xl font-semibold">Test Console sign-in</h1>
      <p className="text-sm text-ink-dim">
        Same accounts and role rules as the main app. This console keeps its own session.
        {fileHint && (
          <>
            {' '}Test sign-in details are in <span className="num break-all text-ink">{fileHint}</span>.
          </>
        )}
      </p>
      <form
        className="grid gap-3"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          login.mutate()
        }}
      >
        <Field label="Username">
          <input className={input} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Password">
          <input className={input} type="password" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <button className={button} disabled={!username || !password || login.isPending}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
        {login.error && <ErrorNote error={login.error} />}
      </form>
    </main>
  )
}

// ------------------------------------------------------------------ live entries
function EntryLog({ lines }: { lines: string[] }) {
  if (!lines.length) return null
  return (
    <ul className="mt-3 grid gap-1 text-sm" aria-live="polite">
      {lines.map((l, i) => (
        <li key={i} className="num rounded bg-deck px-2 py-1 text-ink-dim">{l}</li>
      ))}
    </ul>
  )
}

function MeasurementForm({ astronautId }: { astronautId: number }) {
  const qc = useQueryClient()
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: tc.metrics })
  const [code, setCode] = useState('heart_rate')
  const [value, setValue] = useState('')
  const [context, setContext] = useState('rest')
  const [log, setLog] = useState<string[]>([])
  const metric = metrics.data?.find((m) => m.code === code)
  const save = useMutation({
    mutationFn: () =>
      tc.addMeasurement({ astronaut_id: astronautId, metric_code: code, value: Number(value), unit: metric!.unit, context }),
    onSuccess: (r) => {
      const sev = r.assessment.severity ?? r.assessment.status
      setLog((l) => [`#${r.measurement.id} ${metric!.display_name} ${r.measurement.value} ${r.measurement.unit} (${context}) → ${sev}${r.alert_id ? `, alert #${r.alert_id}` : ''}`, ...l].slice(0, 8))
      setValue('')
      qc.invalidateQueries({ queryKey: ['state'] })
    },
  })
  return (
    <Section title="Measurement" aside="activity context required">
      {metrics.error && <ErrorNote error={metrics.error} />}
      <form
        className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field label="Metric">
          <select className={input} value={code} onChange={(e) => {
            setCode(e.target.value)
            const m = metrics.data?.find((x) => x.code === e.target.value)
            if (m && !m.allowed_contexts.includes(context)) setContext(m.allowed_contexts[0])
          }}>
            {metrics.data?.map((m) => <option key={m.code} value={m.code}>{m.display_name}</option>)}
          </select>
        </Field>
        <Field label={`Value${metric ? ` (${metric.unit})` : ''}`}>
          <input className={`${input} num`} inputMode="decimal" required value={value} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <Field label="Context">
          <select className={input} value={context} onChange={(e) => setContext(e.target.value)}>
            {(metric?.allowed_contexts ?? ['rest']).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <button className={button} disabled={!metric || value === '' || Number.isNaN(Number(value)) || save.isPending}>Add</button>
      </form>
      {save.error && <div className="mt-2"><ErrorNote error={save.error} /></div>}
      <EntryLog lines={log} />
    </Section>
  )
}

function CheckinForm({ astronautId }: { astronautId: number }) {
  const items = useQuery({ queryKey: ['checkin-items'], queryFn: tc.checkinItems })
  const [ratings, setRatings] = useState<Record<string, number>>({})
  const [note, setNote] = useState('')
  const [log, setLog] = useState<string[]>([])
  const save = useMutation({
    mutationFn: () => tc.addCheckin({ astronaut_id: astronautId, ratings, note }),
    onSuccess: (r) => {
      setLog((l) => [`check-in ${r.checkin_id.slice(0, 8)}: ${r.items} item(s), ${r.data_origin}`, ...l].slice(0, 5))
      setRatings({})
      setNote('')
    },
  })
  return (
    <Section title="Daily check-in" aside="1–5 self-ratings, not clinical scales">
      {items.error && <ErrorNote error={items.error} />}
      <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
        <div className="grid gap-2 sm:grid-cols-2">
          {items.data?.map((it) => (
            <fieldset key={it.key} className="grid gap-1 text-sm">
              <legend className="text-ink-dim">{it.label} <span className="text-ink-faint">({it.low_anchor} → {it.high_anchor})</span></legend>
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    type="button"
                    key={n}
                    aria-pressed={ratings[it.key] === n}
                    onClick={() => setRatings((r) => ({ ...r, [it.key]: n }))}
                    className={`num size-8 rounded border ${ratings[it.key] === n ? 'border-signal bg-signal-dim text-ink' : 'border-seam-strong bg-deck text-ink-dim'}`}
                  >{n}</button>
                ))}
              </div>
            </fieldset>
          ))}
        </div>
        <Field label="Note (optional)">
          <input className={input} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <button className={`${button} justify-self-start`} disabled={!Object.keys(ratings).length || save.isPending}>Save check-in</button>
      </form>
      {save.error && <div className="mt-2"><ErrorNote error={save.error} /></div>}
      <EntryLog lines={log} />
    </Section>
  )
}

function SymptomForm({ astronautId }: { astronautId: number }) {
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: tc.catalog })
  const [code, setCode] = useState('')
  const [severity, setSeverity] = useState('mild')
  const [note, setNote] = useState('')
  const [log, setLog] = useState<string[]>([])
  const save = useMutation({
    mutationFn: () => tc.addSymptom({ astronaut_id: astronautId, symptom_code: code, severity, note }),
    onSuccess: (r) => {
      setLog((l) => [`#${r.id} ${r.symptom_code} (${r.severity}), ${r.data_origin}`, ...l].slice(0, 5))
      setNote('')
    },
  })
  return (
    <Section title="Symptom">
      {catalog.error && <ErrorNote error={catalog.error} />}
      <form className="grid gap-3 sm:grid-cols-[2fr_1fr_2fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
        <Field label="Symptom">
          <select className={input} value={code} onChange={(e) => setCode(e.target.value)} required>
            <option value="">Choose…</option>
            {catalog.data?.symptoms.map((s) => (
              <option key={s.code} value={s.code}>{s.label} · {s.body_system_label}{s.report_promptly ? ' · report promptly' : ''}</option>
            ))}
          </select>
        </Field>
        <Field label="Severity">
          <select className={input} value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {(catalog.data?.severities ?? ['mild', 'moderate', 'severe']).map((s) => <option key={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="Note (optional)">
          <input className={input} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <button className={button} disabled={!code || save.isPending}>Report</button>
      </form>
      {save.error && <div className="mt-2"><ErrorNote error={save.error} /></div>}
      <EntryLog lines={log} />
    </Section>
  )
}

function WearableForm({ astronautId }: { astronautId: number }) {
  const [hours, setHours] = useState(2)
  const [preset, setPreset] = useState('nominal')
  const run = useMutation({ mutationFn: () => tc.wearable(astronautId, hours, preset) })
  return (
    <Section title="Simulated wearable feed" aside="SIMULATED_GENERATOR">
      <p className="mb-3 text-sm text-ink-dim">
        Fills the last hours of the open mission day with SIMULATED smart-shirt readings (heart rate, SpO2,
        temperature). Values are arbitrary demonstration draws, not physiological norms.
      </p>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate() }}>
        <Field label="Hours"><input className={`${input} num w-20`} type="number" min={1} max={12} value={hours}
          onChange={(e) => setHours(Number(e.target.value))} /></Field>
        <Field label="Preset">
          <select className={input} value={preset} onChange={(e) => setPreset(e.target.value)}>
            <option value="nominal">nominal</option>
            <option value="elevated_heart_rate">elevated heart rate</option>
            <option value="fever">fever-like temperature</option>
          </select>
        </Field>
        <button className={button} disabled={run.isPending}>{run.isPending ? 'Generating…' : 'Generate'}</button>
      </form>
      {run.error && <div className="mt-2"><ErrorNote error={run.error} /></div>}
      {run.data && <p className="mt-2 text-sm text-sev-normal">{run.data.readings} readings stored ({run.data.data_origin}){run.data.alerts_touched.length ? `, alerts touched: ${run.data.alerts_touched.map((a) => '#' + a).join(', ')}` : ''}.</p>}
    </Section>
  )
}

// ------------------------------------------------------------------ End Day
function RunView({ run }: { run: EndDayRun }) {
  return (
    <div className="grid gap-3">
      <p className="text-sm">
        Run #{run.id} for <span className="num">{run.close_date}</span>:{' '}
        <strong className={run.status === 'completed' ? 'text-sev-normal' : 'text-sev-critical'}>{run.status}</strong>
        {run.replayed && <span className="text-ink-dim"> (already closed; stored result shown, nothing ran again)</span>}
      </p>
      {run.error && <ErrorNote error={new Error(run.error)} />}
      <ol className="grid gap-1 text-sm">
        {Object.entries(STEP_LABELS).map(([key, label], i) => {
          const st = run.steps[key]
          const color = !st ? 'text-ink-faint' : st.status === 'done' ? 'text-sev-normal' : 'text-sev-critical'
          return (
            <li key={key} className="flex gap-2">
              <span className="num w-5 text-ink-faint">{i + 1}.</span>
              <span className="flex-1">{label}</span>
              <span className={color}>{st?.status ?? 'not run'}</span>
            </li>
          )
        })}
      </ol>
      {Object.values(run.results).length > 0 && (
        <ul className="grid gap-2 text-sm">
          {Object.values(run.results).map((m) => (
            <li key={m.astronaut_id} className="rounded bg-deck px-3 py-2">
              <span className="num">{m.code}</span> closed mission day <span className="num">{m.closed_mission_day}</span>
              {m.warnings.length ? (
                <ul className="mt-1 list-disc pl-5 text-sev-watch">{m.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              ) : (
                <span className="text-ink-dim"> · all daily logs present</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EndDayPanel({ state }: { state: TestState }) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const runs = useQuery({ queryKey: ['runs'], queryFn: tc.runs })
  const end = useMutation({
    mutationFn: () => tc.endDay(state.mission_date),
    onSuccess: () => {
      setConfirming(false)
      qc.invalidateQueries()
    },
  })
  const latest = end.data ?? runs.data?.[0]
  return (
    <Section title="End Day" aside={<span className="num">mission date {state.mission_date}</span>}>
      <p className="mb-3 text-sm text-ink-dim">
        Closes mission date <span className="num text-ink">{state.mission_date}</span> for the whole crew (one shared
        mission clock), runs the daily analysis, then moves mission time to 07:00 of the next day. Mission time only
        moves forward. Missing daily logs are warned about, never blocking.
      </p>
      {!confirming ? (
        <button className={button} onClick={() => setConfirming(true)}>End Day {state.mission_date}</button>
      ) : (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-sev-watch/50 bg-sev-watch/10 p-3 text-sm">
          <span>Close {state.mission_date} for all crew members? This cannot be undone.</span>
          <button className={button} disabled={end.isPending} onClick={() => end.mutate()}>
            {end.isPending ? 'Running…' : 'Yes, end the day'}
          </button>
          <button className="h-9 px-3 text-ink-dim hover:text-ink" onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
      {end.error && <div className="mt-3"><ErrorNote error={end.error} /></div>}
      {latest && <div className="mt-4"><RunView run={latest} /></div>}
      <MissionReturn />
    </Section>
  )
}

function MissionReturn() {
  const m = useMutation({ mutationFn: tc.missionReturn })
  return (
    <div className="mt-6 rounded-md border border-seam p-3 text-sm">
      <p className="text-ink-dim">Mission return (doctor account): AMC Core opens for good so the full mission log can be read.</p>
      <button className="mt-2 h-8 rounded border border-seam-strong px-3 hover:border-signal" disabled={m.isPending} onClick={() => m.mutate()}>Mark mission returned</button>
      {m.error && <div className="mt-2"><ErrorNote error={m.error} /></div>}
      {m.data && <p className="mt-2 text-sev-normal">Mission returned: AMC Core is open.</p>}
    </div>
  )
}

// ------------------------------------------------------------------ scenarios
function ScenarioPanel({ crew, user }: { crew: CrewDay[]; user: User }) {
  const qc = useQueryClient()
  const fixtures = useQuery({ queryKey: ['fixtures'], queryFn: tc.fixtures })
  const [target, setTarget] = useState<number>(user.astronaut_id ?? crew[0]?.astronaut_id ?? 0)
  const load = useMutation({
    mutationFn: (code: string) => tc.loadFixture(target, code),
    onSuccess: () => qc.invalidateQueries(),
  })
  return (
    <Section title="Scenario fixtures" aside="SIMULATED_GENERATOR">
      <p className="mb-3 text-sm text-ink-dim">
        Back-fills one astronaut's mission with SIMULATED data. Only possible while that astronaut has no records yet.
      </p>
      {user.role === 'doctor' && (
        <Field label="Crew member">
          <select className={`${input} max-w-xs`} value={target} onChange={(e) => setTarget(Number(e.target.value))}>
            {crew.map((c) => <option key={c.astronaut_id} value={c.astronaut_id}>{c.code} · {c.display_name}</option>)}
          </select>
        </Field>
      )}
      {fixtures.error && <ErrorNote error={fixtures.error} />}
      <ul className="mt-3 grid gap-2">
        {fixtures.data?.map((f) => (
          <li key={f.code} className="grid gap-2 rounded-md border border-seam p-3 sm:grid-cols-[1fr_auto] sm:items-center">
            <div>
              <p className="font-medium">{f.title} <span className="num text-xs text-ink-faint">{f.days} days</span></p>
              <p className="text-sm text-ink-dim">{f.description}</p>
              {f.symptoms.length > 0 && <p className="text-xs text-ink-faint">Symptoms: {f.symptoms.join(', ')}</p>}
            </div>
            <button className={button} disabled={!target || load.isPending} onClick={() => load.mutate(f.code)}>Load</button>
          </li>
        ))}
      </ul>
      {load.error && <div className="mt-3"><ErrorNote error={load.error} /></div>}
      {load.data && (
        <p className="mt-3 text-sm text-sev-normal">
          Loaded {load.data.fixture}: {load.data.measurements} measurements, {load.data.checkins} check-in items,{' '}
          {load.data.symptoms} symptoms. Mission day is now {load.data.mission_day}.
        </p>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ cabin simulator
function CabinRow({ c }: { c: CabinControl }) {
  const qc = useQueryClient()
  const [drift, setDrift] = useState(String(c.drift))
  const done = () => qc.invalidateQueries({ queryKey: ['cabin'] })
  const fault = useMutation({ mutationFn: (f: string) => tc.cabinFault(c.id, f), onSuccess: done })
  const drifted = useMutation({ mutationFn: () => tc.cabinDrift(c.id, Number(drift)), onSuccess: done })
  const result = fault.data ?? drifted.data
  return (
    <li className="grid gap-2 rounded-md border border-seam p-3 text-sm sm:grid-cols-[1fr_auto_auto] sm:items-end">
      <div>
        <p className="font-medium">{c.zone_name} · {c.variable}</p>
        <p className="num text-xs text-ink-dim">reading {c.reading ?? '—'} {c.unit} · setpoint {c.setpoint} · {c.mode}</p>
      </div>
      <Field label="Fault">
        <select className={input} value={c.fault} onChange={(e) => fault.mutate(e.target.value)}>
          <option value="none">none</option><option value="sensor">sensor</option><option value="actuator">actuator</option>
        </select>
      </Field>
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); drifted.mutate() }}>
        <Field label="Drift"><input className={`${input} num w-20`} value={drift} onChange={(e) => setDrift(e.target.value)} /></Field>
        <button className={button} disabled={drifted.isPending || Number.isNaN(Number(drift))}>Apply</button>
      </form>
      {(fault.error || drifted.error) && <div className="sm:col-span-3"><ErrorNote error={fault.error ?? drifted.error} /></div>}
      {result && <p className="text-xs text-sev-watch sm:col-span-3">{result.alerts_raised.length ? `Environment alerts raised: ${result.alerts_raised.map((a) => '#' + a).join(', ')}` : 'No alert raised.'}</p>}
    </li>
  )
}

function CabinPanel({ user }: { user: User }) {
  const q = useQuery({ queryKey: ['cabin', user.astronaut_id], queryFn: () => tc.cabinState(user.role === 'doctor' ? null : user.astronaut_id), refetchInterval: 5_000 })
  const controls = (q.data?.controls ?? []).filter((c) => user.role === 'doctor' || c.astronaut_id === user.astronaut_id)
  return (
    <Section title="Cabin simulator" aside="SIMULATED cabin">
      <p className="mb-3 text-sm text-ink-dim">
        Inject sensor or actuator faults and drift readings. AMC pauses autonomy for that variable and raises an
        environment alert when a reading leaves the envelope or a fault occurs. Cabin pressure is doctor-only.
      </p>
      {q.error && <ErrorNote error={q.error} />}
      <ul className="grid gap-2">{controls.map((c) => <CabinRow key={c.id} c={c} />)}</ul>
    </Section>
  )
}

// ------------------------------------------------------------------ shell
type Tab = 'log' | 'endday' | 'scenarios' | 'cabin'

function Console({ user }: { user: User }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>(user.role === 'astronaut' ? 'log' : 'endday')
  const state = useQuery({ queryKey: ['state'], queryFn: tc.state, refetchInterval: 30_000 })
  const logout = useMutation({ mutationFn: tc.logout, onSuccess: () => qc.invalidateQueries() })
  const me = state.data?.crew.find((c) => c.astronaut_id === user.astronaut_id)
  const tabs: [Tab, string][] = [['log', 'Live log'], ['endday', 'End Day'], ['scenarios', 'Scenarios'], ['cabin', 'Cabin']]
  return (
    <div className="mx-auto grid max-w-5xl gap-6 px-4 py-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">AMC Test Console</h1>
          <p className="text-sm text-ink-dim">
            {user.display_name} · {user.role}
            {me && <> · mission day <span className="num text-ink">{me.mission_day}</span></>}
            {state.data && <> · mission time <span className="num">{fmtTime(state.data.mission_now)}</span></>}
          </p>
        </div>
        <button className="h-9 rounded-md border border-seam-strong px-3 text-sm hover:border-signal" onClick={() => logout.mutate()}>
          Sign out
        </button>
      </header>
      {state.error && <ErrorNote error={state.error} />}
      <nav aria-label="Test Console" className="flex gap-1 border-b border-seam">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            aria-current={tab === key ? 'page' : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === key ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}
          >{label}</button>
        ))}
      </nav>
      {tab === 'log' && (user.astronaut_id ? (
        <div className="grid gap-8">
          <p className="text-sm text-ink-dim">
            Entries are timestamped at mission time now and tagged <span className="num">MANUAL_TEST_ENTRY</span>.
            They appear in the main app and the doctor panel through the shared backend.
          </p>
          <MeasurementForm astronautId={user.astronaut_id} />
          <CheckinForm astronautId={user.astronaut_id} />
          <SymptomForm astronautId={user.astronaut_id} />
          <WearableForm astronautId={user.astronaut_id} />
        </div>
      ) : (
        <p className="text-sm text-ink-dim">Live logs are entered by astronaut accounts. Sign out and sign in as a crew member.</p>
      ))}
      {tab === 'endday' && state.data && <EndDayPanel state={state.data} />}
      {tab === 'scenarios' && state.data && <ScenarioPanel crew={state.data.crew} user={user} />}
      {tab === 'cabin' && <CabinPanel user={user} />}
      {state.data && (
        <Section title="Crew mission days" aside={state.data.day_lock_through ? `closed through ${fmtTime(state.data.day_lock_through)}` : 'no day closed yet'}>
          <ul className="grid gap-1 text-sm">
            {state.data.crew.map((c) => (
              <li key={c.astronaut_id} className="flex justify-between rounded bg-deck px-3 py-1.5">
                <span><span className="num">{c.code}</span> {c.display_name}</span>
                <span className="num text-ink-dim">day {c.mission_day} · since {c.mission_start}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <footer className="pb-6 text-xs text-ink-faint">
        All data here is SIMULATED test data. Alert bands are demonstration values, not clinically validated.
      </footer>
    </div>
  )
}

export function TestConsole() {
  const auth = useQuery({ queryKey: ['auth'], queryFn: tc.authState })
  return (
    <div className="min-h-screen">
      <TestBanner />
      {auth.isLoading && <p className="p-6 text-ink-dim">Loading…</p>}
      {auth.error && <div className="p-6"><ErrorNote error={auth.error} /></div>}
      {auth.data && (auth.data.signed_in && auth.data.user ? <Console user={auth.data.user} /> : <SignIn fileHint={auth.data.test_accounts_file} />)}
    </div>
  )
}
