// API client for the Test Console. Same backend as the main app; the dev proxy adds
// X-AM-Client: test-console so this console keeps its own session cookie.
import { ApiError } from '../api/client'

export { ApiError }

type Query = Record<string, string | number | undefined>

async function call<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const qs = query
    ? '?' + new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]))
    : ''
  let res: Response
  try {
    res = await fetch(`/api${path}${qs}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'The AM backend is not reachable on port 8000.')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 404 && path.startsWith('/test')) {
      detail = 'The test API is off. Start the backend with AM_TEST_MODE=1.'
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export interface User {
  id: number
  username: string
  display_name: string
  role: 'astronaut' | 'doctor'
  astronaut_id: number | null
  is_test_account: boolean
}
export interface AuthState {
  signed_in: boolean
  user: User | null
  test_accounts_file: string | null
  mission_now: string
}
export interface CrewDay {
  astronaut_id: number
  code: string
  display_name: string
  mission_start: string
  mission_day: number
}
export interface RunBrief {
  id: number
  close_date: string
  status: string
  started_at: string
  finished_at: string | null
}
export interface TestState {
  simulated_label: string
  mission_now: string
  mission_date: string
  clock_offset_seconds: number
  day_lock_through: string | null
  crew: CrewDay[]
  last_runs: RunBrief[]
}
export interface Metric {
  code: string
  category: string
  display_name: string
  unit: string
  allowed_contexts: string[]
  plausible_min: number | null
  plausible_max: number | null
}
export interface CheckinItem {
  key: string
  label: string
  low_anchor: string
  high_anchor: string
}
export interface CatalogItem {
  code: string
  label: string
  body_system_label: string
  report_promptly: boolean
}
export interface Catalog {
  severities: string[]
  symptoms: CatalogItem[]
}
export interface IngestResult {
  measurement: { id: number; value: number; unit: string; quality: string; validation_notes: string[] }
  assessment: { status: string; severity: string | null; z: number | null; persistence_count: number; rule_id: string | null }
  alert_id: number | null
}
export interface StepState {
  status: string
  at: string
  detail: Record<string, unknown>
}
export interface MemberResult {
  astronaut_id: number
  code: string
  closed_mission_day: number
  warnings: string[]
}
export interface EndDayRun {
  id: number
  close_date: string
  status: string
  replayed: boolean
  error: string | null
  steps: Record<string, StepState>
  results: Record<string, MemberResult>
  mission_now: string
  mission_date: string
  finished_at: string | null
}
export interface Fixture {
  code: string
  title: string
  description: string
  days: number
  symptoms: string[]
}
export interface FixtureLoad {
  fixture: string
  mission_day: number
  measurements: number
  checkins: number
  symptoms: number
  data_origin: string
}

export interface CabinControl {
  id: number
  zone_name: string
  astronaut_id: number | null
  variable: string
  unit: string
  mode: string
  setpoint: number
  reading: number | null
  fault: string
  drift: number
}

export const tc = {
  authState: () => call<AuthState>('GET', '/v1/auth/state'),
  login: (username: string, password: string) =>
    call<User>('POST', '/v1/auth/login', { username, password, device_name: 'Test Console' }),
  logout: () => call<unknown>('POST', '/v1/auth/logout'),
  state: () => call<TestState>('GET', '/test/state'),
  metrics: () => call<Metric[]>('GET', '/v1/metrics'),
  checkinItems: () => call<CheckinItem[]>('GET', '/v1/checkins/items'),
  catalog: () => call<Catalog>('GET', '/v1/symptoms/catalog'),
  addMeasurement: (b: { astronaut_id: number; metric_code: string; value: number; unit: string; context: string; recorded_at?: string }) =>
    call<IngestResult>('POST', '/test/measurements', b),
  addCheckin: (b: { astronaut_id: number; ratings: Record<string, number>; note: string }) =>
    call<{ checkin_id: string; items: number; data_origin: string }>('POST', '/test/checkins', b),
  addSymptom: (b: { astronaut_id: number; symptom_code: string; severity: string; note: string }) =>
    call<{ id: number; symptom_code: string; severity: string; data_origin: string }>('POST', '/test/symptoms', b),
  endDay: (close_date: string) => call<EndDayRun>('POST', '/test/end-day', { close_date }),
  runs: () => call<EndDayRun[]>('GET', '/test/end-day/runs', undefined, { limit: 10 }),
  fixtures: () => call<Fixture[]>('GET', '/test/scenarios'),
  missionReturn: () => call<{ open: boolean }>('POST', '/test/mission/return'),
  cabinState: (astronautId: number | null) =>
    astronautId == null
      ? call<{ controls: CabinControl[] }>('GET', '/v1/cabin/overview')
      : call<{ controls: CabinControl[] }>('GET', '/v1/cabin/state', undefined, { astronaut_id: astronautId }),
  cabinFault: (control_id: number, fault: string) => call<{ alerts_raised: number[] }>('POST', '/test/cabin/fault', { control_id, fault }),
  cabinDrift: (control_id: number, offset: number) => call<{ alerts_raised: number[] }>('POST', '/test/cabin/drift', { control_id, offset }),
  wearable: (astronaut_id: number, hours: number, preset: string) =>
    call<{ readings: number; alerts_touched: number[]; data_origin: string }>('POST', '/test/wearable', { astronaut_id, hours, preset }),
  loadFixture: (astronaut_id: number, scenario: string) =>
    call<FixtureLoad>('POST', '/test/scenarios/load', { astronaut_id, scenario }),
}
