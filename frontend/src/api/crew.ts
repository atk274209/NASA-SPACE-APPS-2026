// API calls for feature F1: manual measurement entry, radiation log, crew and missions.
// Types are hand-written to match the Pydantic schemas in backend/am/api/v1/crew.py.
import { request, type Astronaut } from './client'

export interface EntryUnit {
  unit: string
  label: string
  canonical: boolean
  /** canonical value = (entered value + offset) × factor */
  factor: number
  offset: number
  note: string
  source: string
  aliases: string[]
}

export interface EntryMetric {
  code: string
  display_name: string
  category: string
  category_label: string
  unit: string
  unit_label: string
  accepted_units: EntryUnit[]
  contexts: string[]
  plausible_min: number | null
  plausible_max: number | null
  sanity_note: string
  bound_note: string
  baseline_enabled: boolean
  alert_rule_id: string | null
}

export interface EntryChoice {
  code: string
  label: string
  description: string
}

export interface EntrySpec {
  metrics: EntryMetric[]
  categories: EntryChoice[]
  contexts: EntryChoice[]
  default_source_name: string
  max_future_minutes: number
  degraded_confidence_below: number
  degraded_signal_quality: string[]
  threshold_label: string
}

export interface ManualEntryIn {
  astronaut_id: number
  metric_code: string
  value: number
  unit: string
  context: string
  recorded_at?: string | null
  source_name?: string
  confidence?: number | null
  signal_quality?: string | null
}

export interface EntryConversion {
  entered_value: number
  entered_unit: string
  entered_label: string
  canonical_value: number
  canonical_unit: string
  canonical_label: string
  formula: string
  source: string
}

export interface ManualEntryRow {
  id: number
  astronaut_id: number
  metric_code: string
  display_name: string
  value: number
  unit: string
  unit_label: string
  conversion: EntryConversion | null
  context: string
  recorded_at: string
  ingested_at: string
  source_name: string
  is_simulated: boolean
  quality: string
  accepted: boolean
  confidence: number | null
  validation_notes: string[]
  assessment_status: string | null
  severity: string | null
  deviation_score: number | null
  alert_id: number | null
}

export interface EntryAssessment {
  status: string
  severity: string | null
  instant_severity: string | null
  z: number | null
  direction: string | null
  persistence_count: number
  baseline_profile_id: number | null
  rule_id: string | null
  reasons: string[]
}

export interface EntryAlert {
  id: number
  severity: string
  peak_severity: string
  status: string
  threshold_label: string
}

export interface EntryBaselineEvent {
  action: string
  profile_id: number | null
  reason: string
}

export interface ManualEntryResult {
  accepted: boolean
  entry: ManualEntryRow
  assessment: EntryAssessment
  alert: EntryAlert | null
  baseline_event: EntryBaselineEvent | null
  threshold_label: string
}

export interface MissionEnvironment {
  code: string
  label: string
  description: string
}

export interface MissionCreateIn {
  astronaut_id: number
  name: string
  start_date: string
  planned_days?: number | null
  environment: string
  actor?: string
}

export interface MissionDetail {
  id: number
  name: string
  astronaut_id: number
  start_date: string
  planned_days: number | null
  environment: string
  environment_label: string
  is_simulated: boolean
  created_at: string
  is_current: boolean
}

export interface RadiationDay {
  utc_date: string
  mission_day: number | null
  dose: number
  entries: number
  cumulative: number
  simulated: boolean
}

export interface RadiationCumulative {
  astronaut_id: number
  unit: string
  days: RadiationDay[]
  total: number
  entries: number
  mission_start: string | null
  simulated: boolean
  note: string
}

export const crewApi = {
  entrySpec: () => request<EntrySpec>('GET', '/entry-spec'),
  createEntry: (body: ManualEntryIn) => request<ManualEntryResult>('POST', '/manual-entries', body),
  recentEntries: (astronaut_id: number, limit = 10) =>
    request<ManualEntryRow[]>('GET', '/manual-entries', undefined, { astronaut_id, limit }),
  radiation: (astronaut_id: number) =>
    request<RadiationCumulative>('GET', '/radiation/cumulative', undefined, { astronaut_id }),
  environments: () => request<MissionEnvironment[]>('GET', '/missions/environments'),
  missions: (astronaut_id: number) => request<MissionDetail[]>('GET', '/missions', undefined, { astronaut_id }),
  createMission: (body: MissionCreateIn) => request<MissionDetail>('POST', '/missions', body),
  createAstronaut: (code: string, display_name: string) =>
    request<Astronaut>('POST', '/astronauts', { code, display_name }),
}
