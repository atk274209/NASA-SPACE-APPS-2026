import type { QueryClient } from '@tanstack/react-query'
import { request } from './client'

/*
 * Feature F7: "Is this serious?" guidance, escalation events and the body map.
 * Hand-written mirror of backend/am/guidance/schemas.py; keep both in step.
 */

export type GuidanceLevel = 'WATCH' | 'WARNING' | 'CRITICAL'
export type BodyStatus = 'red' | 'yellow' | 'watch' | 'green' | 'no_data'
export type SubjectType = 'alert' | 'symptom_report'
export type EscalationStatus = 'open' | 'acknowledged' | 'closed'

export interface LevelGuidance {
  level: GuidanceLevel
  procedure_id: string
  plain_meaning: string
  who_to_involve: string
  guidance: string
  raises_event: boolean
}

export interface GuidanceLevels {
  version: string
  label: string
  levels: LevelGuidance[]
  symptom_rules: { when: string; level: GuidanceLevel }[]
  report_promptly_label: string
  report_promptly_symptoms: { code: string; label: string }[]
  disclaimer: string
}

export interface EscalationAlert {
  id: number
  metric_code: string
  display_name: string
  context: string
  direction: string
  severity: string
  peak_severity: string
  status: string
  is_simulated: boolean
}

export interface Escalation {
  id: number
  astronaut_id: number
  alert_id: number | null
  level: GuidanceLevel
  procedure_id: string
  status: EscalationStatus
  plain_meaning: string
  who_to_involve: string
  guidance: string
  label: string
  data_time: string | null
  acknowledged_by: string | null
  acknowledged_at: string | null
  acknowledgement_note: string | null
  created_at: string
  updated_at: string
  alert: EscalationAlert | null
}

export interface GuidanceSubject {
  type: SubjectType
  id: number
  astronaut_id: number
  title: string
  status: string
  is_simulated: boolean
}

export interface Guidance {
  subject: GuidanceSubject
  level: GuidanceLevel
  procedure_id: string
  plain_meaning: string
  who_to_involve: string
  guidance: string
  label: string
  matrix_version: string
  applies_now: boolean
  reasons: string[]
  escalations: Escalation[]
}

export interface CurrentItem {
  type: SubjectType
  id: number
  title: string
  level: GuidanceLevel
  reason: string
}

export interface CurrentGuidance {
  astronaut_id: number
  level: GuidanceLevel | null
  guidance: LevelGuidance | null
  items: CurrentItem[]
  open_escalations: number
  label: string
  matrix_version: string
  note: string
}

export interface BodyReason {
  text: string
  level: GuidanceLevel | null
  ref_type: string | null
  ref_id: number | null
}

export interface SystemReading {
  source: 'measurement' | 'self_report'
  record_id: number
  code: string
  display_name: string
  context: string
  value: number
  unit: string
  recorded_at: string
  severity: string | null
  assessment_status: string | null
  is_simulated: boolean
}

export interface SystemSymptom {
  id: number
  symptom_code: string
  label: string
  severity: string
  report_promptly: boolean
  level: GuidanceLevel
  reported_at: string
  onset_at: string | null
  source: string
  is_simulated: boolean
}

export interface SystemAlert {
  id: number
  metric_code: string
  display_name: string
  context: string
  direction: string
  severity: string
  peak_severity: string
  status: string
  level: GuidanceLevel
  last_seen_at: string
  is_simulated: boolean
}

export interface SystemDocument {
  slug: string
  title: string
  organization: string
  url: string
  matched_topics: string[]
}

export interface BodySystem {
  code: string
  label: string
  status: BodyStatus
  level: GuidanceLevel | null
  coverage: string
  reasons: BodyReason[]
  metrics: SystemReading[]
  active_symptoms: SystemSymptom[]
  open_alerts: SystemAlert[]
  documents: SystemDocument[]
  last_data_at: string | null
}

export interface BodyMapData {
  astronaut_id: number
  generated_at: string
  simulated: boolean
  label: string
  rules: { status: BodyStatus; meaning: string }[]
  rules_note: string
  knowledge_indexed: boolean
  systems: BodySystem[]
}

export const guidanceApi = {
  levels: () => request<GuidanceLevels>('GET', '/guidance/levels'),
  current: (astronaut_id: number) =>
    request<CurrentGuidance>('GET', '/guidance/current', undefined, { astronaut_id }),
  forAlert: (alertId: number) => request<Guidance>('GET', `/guidance/alerts/${alertId}`),
  forSymptom: (reportId: number) => request<Guidance>('GET', `/guidance/symptoms/${reportId}`),
  escalations: (astronaut_id: number, status?: EscalationStatus) =>
    request<Escalation[]>('GET', '/escalations', undefined, { astronaut_id, status }),
  acknowledgeEscalation: (id: number, actor: string, note: string) =>
    request<Escalation>('POST', `/escalations/${id}/acknowledge`, { actor, note }),
  bodyMap: (astronaut_id: number) => request<BodyMapData>('GET', '/bodymap', undefined, { astronaut_id }),
}

/** Refresh everything that shows guidance, escalation or body-map state after a review action. */
export function invalidateGuidance(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ['guidance'] })
  qc.invalidateQueries({ queryKey: ['escalations'] })
  qc.invalidateQueries({ queryKey: ['bodymap'] })
}
