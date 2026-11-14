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
export type AIStatus = S['AIStatusOut']
export type Conversation = S['ConversationOut']
export type ChatMessage = S['MessageOut']
export type Turn = S['TurnOut']
export type Suggestion = S['SuggestionOut']
export type MemoryItem = S['MemoryOut']
export type CheckinItem = S['CheckinItemOut']
export type SelfReport = S['SelfReportOut']
export type Finding = S['FindingOut']
export type AnalysisRun = S['RunOut']
export type DailySeriesOut = S['SeriesOut']
export type Observation = S['ObservationOut']
export type JournalEntry = S['JournalOut']
export type DailyTask = S['TaskOut']
export type Today = S['TodayOut']
export type KnowledgeDoc = S['DocumentOut']
export type IngestReport = S['KnowledgeIngestOut']
export type SearchHit = {
  chunk_id: number; doc_slug: string; title: string; organization: string; published_date: string | null
  url: string; kind: string; text: string; score: number; method: string
}

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
  aiStatus: () => request<AIStatus>('GET', '/ai/status'),
  conversations: (astronaut_id: number) =>
    request<Conversation[]>('GET', '/companion/conversations', undefined, { astronaut_id }),
  createConversation: (astronaut_id: number) =>
    request<Conversation>('POST', '/companion/conversations', { astronaut_id }),
  messages: (conversationId: number) => request<ChatMessage[]>('GET', `/companion/conversations/${conversationId}/messages`),
  send: (conversationId: number, content: string) =>
    request<Turn>('POST', `/companion/conversations/${conversationId}/messages`, { content }),
  suggestions: (astronaut_id: number) =>
    request<Suggestion[]>('GET', '/companion/suggestions', undefined, { astronaut_id }),
  memory: (astronaut_id: number, layer?: string) => request<MemoryItem[]>('GET', '/memory', undefined, { astronaut_id, layer }),
  checkinItems: () => request<CheckinItem[]>('GET', '/checkins/items'),
  submitCheckin: (astronaut_id: number, ratings: Record<string, number>, note: string) =>
    request<SelfReport[]>('POST', '/checkins', { astronaut_id, ratings, note }),
  checkins: (astronaut_id: number) => request<SelfReport[]>('GET', '/checkins', undefined, { astronaut_id }),
  runCorrelation: (astronaut_id: number) => request<AnalysisRun>('POST', '/research/correlation/run', { astronaut_id }),
  latestRun: (astronaut_id: number) =>
    request<AnalysisRun | null>('GET', '/research/correlation/latest', undefined, { astronaut_id }),
  series: (astronaut_id: number) => request<DailySeriesOut[]>('GET', '/research/series', undefined, { astronaut_id }),
  observations: (astronaut_id: number) =>
    request<Observation[]>('GET', '/research/observations', undefined, { astronaut_id }),
  createObservation: (finding_id: number, use_llm = true) =>
    request<Observation>('POST', '/research/observations', { finding_id, use_llm }),
  reviewObservation: (id: number, status: string, actor: string) =>
    request<Observation>('POST', `/research/observations/${id}/review`, { status, actor }),
  generateJournal: (astronaut_id: number) => request<JournalEntry[]>('POST', '/journal/generate', { astronaut_id }),
  journal: (astronaut_id: number) => request<JournalEntry[]>('GET', '/journal', undefined, { astronaut_id }),
  today: (astronaut_id: number) => request<Today>('GET', '/missions/today', undefined, { astronaut_id }),
  setTask: (id: number, status: string) => request<DailyTask>('POST', `/missions/tasks/${id}`, { status }),
  missionHistory: (astronaut_id: number) =>
    request<{ mission_day: number; done: number; total: number }[]>('GET', '/missions/history', undefined, { astronaut_id }),
  ingestKnowledge: () => request<IngestReport>('POST', '/knowledge/ingest'),
  documents: () => request<KnowledgeDoc[]>('GET', '/knowledge/documents'),
  searchKnowledge: (q: string) => request<SearchHit[]>('GET', '/knowledge/search', undefined, { q, k: 6 }),
  audit: (limit = 100) => request<AuditEntry[]>('GET', '/audit', undefined, { limit }),
}
