import { request } from './client'

/* Hand-written types for the journal (F4). They match JournalOut in backend/am/api/v1/research.py,
   with `sections` typed as written by am/journal/service.py, and the schemas in
   backend/am/api/v1/journal_ext.py. */

export type DayLevel = 'none' | 'green' | 'yellow' | 'red'

/** Entries written before day marking existed carry this generator version and no day level. */
export const UNMARKED_GENERATOR_VERSIONS = ['journal-1']

export type JournalRef = { type: string; id?: number; key?: string; day?: number; days?: number[] }

export interface DayLevelReason {
  rule: string
  level: 'red' | 'yellow'
  text: string
  rule_text: string
  refs: JournalRef[]
}

export interface NarrativeGrounding {
  unverified_numbers?: number[]
  wording_flags?: string[]
  facts_sha256?: string
  input?: { symptoms_reported: number; symptoms_ongoing: number; chat_messages: number; chat_messages_included: number }
  /** service_unreachable | model_not_installed | model_error | malformed_reply | not_requested | null */
  fallback_reason?: string | null
  outdated?: boolean
}

export interface JournalItem {
  key: string
  label: string
  value: number
  unit: string
  aggregation: string
  n: number
  status?: string
  difference?: number
  percent?: number | null
  reference_median?: number
  reference_days?: number[]
}

export interface JournalSymptom {
  id: number
  code: string
  label: string
  category: string
  body_system: string | null
  severity: string
  report_promptly: boolean
  reported_at: string
  reported_day: number
  source: string
  note: string
  status_at_end_of_day: 'active' | 'resolved'
  resolved_at: string | null
  is_simulated: boolean
}

export interface JournalSections {
  physical: JournalItem[]
  mental: JournalItem[]
  tasks: { done: number; total: number; percent: number; open: string[] } | null
  alerts: { new: { id: number; metric: string; context: string; severity: string | null }[]; open_at_end_of_day: number }
  notes: string[]
  /** Missing on entries written before day marking. */
  symptoms?: { reported: JournalSymptom[]; ongoing: JournalSymptom[] }
}

export interface JournalFlag {
  severity: string
  text: string
  alert_id?: number
  key?: string
  baseline_audit_id?: number
}

export interface JournalDay {
  id: number
  astronaut_id: number
  mission_day: number
  day_date: string
  sections: JournalSections
  flags: JournalFlag[]
  observations: { type: string; key: string; text: string; days: number[] }[]
  refs: JournalRef[]
  complete_day: boolean
  is_simulated: boolean
  generator_version: string
  generated_at: string
  day_level: DayLevel
  day_level_reasons: DayLevelReason[]
  narrative: string
  narrative_generated_by: 'llm' | 'deterministic' | null
  narrative_model: string | null
  narrative_grounding: NarrativeGrounding
  narrative_generated_at: string | null
}

export interface DayMessage {
  id: number
  conversation_id: number
  conversation_title: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  device_name: string | null
  client_host: string | null
  generated_by: string
  model: string | null
}

export interface DayLevelRules {
  version: string
  label: string
  note: string
  levels: { level: DayLevel; label: string; description: string }[]
  rules: { id: string; level: 'red' | 'yellow'; text: string; detail: string }[]
}

export const journalApi = {
  list: (astronaut_id: number) => request<JournalDay[]>('GET', '/journal', undefined, { astronaut_id }),
  generate: (astronaut_id: number) => request<JournalDay[]>('POST', '/journal/generate', { astronaut_id }),
  /** Regenerates the day, then drafts its narrative (the page calls it day by day to show progress). */
  writeNarrative: (astronautId: number, day: number, use_llm = true) =>
    request<JournalDay>('POST', `/journal/${astronautId}/days/${day}/narrative`, { use_llm }),
  conversations: (astronautId: number, day: number) =>
    request<DayMessage[]>('GET', `/journal/${astronautId}/days/${day}/conversations`),
  dayLevelRules: () => request<DayLevelRules>('GET', '/journal/day-level-rules'),
}
