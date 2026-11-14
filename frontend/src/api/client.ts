import type { components } from './schema'

type S = components['schemas']
export type Astronaut = S['AstronautOut']
export type Mission = S['MissionOut']
export type Measurement = S['MeasurementOut']
export type Baseline = S['BaselineOut']
export type BaselineAudit = S['BaselineAuditOut']
export type Alert = S['AlertOut']
export type AlertDetail = S['AlertDetailOut']
export type Overview = S['OverviewOut']
export type LatestReading = S['LatestReadingOut']
export type Metric = S['MetricOut']
export type Thresholds = S['ThresholdsOut']
export type ThresholdRule = S['ThresholdRuleOut']
export type SystemStatus = S['SystemStatusOut']
export type Scenario = S['ScenarioOut']
export type SimulationRun = S['SimulationRunOut']
export type SimulationRunIn = S['SimulationRunIn']
export type AuditEntry = S['AuditOut']

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

function qs(query?: Query): string {
  if (!query) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/v1${path}${qs(query)}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'The AM service is not reachable. Start the backend on port 8000.')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const api = {
  status: () => request<SystemStatus>('GET', '/system/status'),
  astronauts: () => request<Astronaut[]>('GET', '/astronauts'),
  overview: (id: number) => request<Overview>('GET', `/astronauts/${id}/overview`),
  metrics: () => request<Metric[]>('GET', '/metrics'),
  thresholds: () => request<Thresholds>('GET', '/thresholds'),
  measurements: (q: { astronaut_id: number; metric_code?: string; context?: string; since?: string; limit?: number }) =>
    request<Measurement[]>('GET', '/measurements', undefined, q),
  baselines: (q: { astronaut_id: number; include_history?: boolean; metric_code?: string }) =>
    request<Baseline[]>('GET', '/baselines', undefined, q),
  baselineAudit: (q: { astronaut_id: number; metric_code?: string; context?: string }) =>
    request<BaselineAudit[]>('GET', '/baselines/audit', undefined, q),
  unfreeze: (id: number, actor: string, reason: string) =>
    request<Baseline>('POST', `/baselines/${id}/unfreeze`, { actor, reason }),
  alerts: (q: { astronaut_id?: number; open_only?: boolean; status?: string }) =>
    request<Alert[]>('GET', '/alerts', undefined, q),
  alert: (id: number) => request<AlertDetail>('GET', `/alerts/${id}`),
  acknowledge: (id: number, actor: string, note: string) =>
    request<AlertDetail>('POST', `/alerts/${id}/acknowledge`, { actor, note }),
  resolve: (id: number, actor: string, note: string) =>
    request<AlertDetail>('POST', `/alerts/${id}/resolve`, { actor, note }),
  scenarios: () => request<Scenario[]>('GET', '/simulation/scenarios'),
  runSimulation: (body: SimulationRunIn) => request<SimulationRun>('POST', '/simulation/run', body),
  audit: (limit = 100) => request<AuditEntry[]>('GET', '/audit', undefined, { limit }),
}
