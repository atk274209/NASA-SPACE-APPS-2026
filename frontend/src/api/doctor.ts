import { request } from './client'

export type FlagLevel = 'none' | 'yellow' | 'red'

export interface FeedItem {
  id: number
  astronaut_id: number
  astronaut_code: string
  astronaut_name: string
  source_type: 'message' | 'symptom_report' | 'checkin_note' | 'diary'
  source_id: number
  conversation_id: number | null
  text: string
  is_simulated: boolean
  created_at: string
  level: FlagLevel
  rule_level: FlagLevel
  rule_ids: string[]
  rule_reasons: string[]
  ai_status: 'pending' | 'done' | 'unavailable' | 'rejected' | 'skipped'
  ai_level: FlagLevel | null
  ai_reason: string
  ai_quote: string
  ai_model: string | null
  approval_status: 'awaiting' | 'approved' | null
  approval_by: string | null
  approval_at: string | null
  approval_note: string
  seen_at: string | null
  seen_by: string | null
}

export interface EscalationItem {
  id: number
  astronaut_id: number
  astronaut_code: string
  astronaut_name: string
  alert_id: number | null
  level: string
  procedure_id: string
  status: string
  plain_meaning: string
  who_to_involve: string
  guidance: string
  label: string
}

export interface CareItem {
  id: number
  source_type: string
  text: string
  created_at: string
  approval_status: 'awaiting' | 'approved' | null
  approval_by: string | null
  approval_at: string | null
  approval_note: string
}

export interface CareStatus {
  awaiting: CareItem[]
  recently_approved: CareItem[]
  protocol_label: string
  protocol: { procedure_id: string; plain_meaning: string; who_to_involve: string; guidance: string }
  am_core_notices: { kind: string; actor: string; reason: string; opened_at: string }[]
}

export interface CrewMember {
  astronaut_id: number
  code: string
  display_name: string
  callsign: string
  crew_role: string
  is_simulated: boolean
  mission_day: number | null
  chronic_conditions: string[]
  unseen_red: number
  unseen_yellow: number
  open_alerts: number
  overdue_exams: number
  level: FlagLevel
  latest: FeedItem | null
}

export const doctorApi = {
  feed: (q: { after_id?: number; limit?: number; min_level?: FlagLevel; unseen?: boolean; astronaut_id?: number } = {}) =>
    request<FeedItem[]>('GET', '/doctor/feed', undefined, q),
  seen: (id: number) => request<FeedItem>('POST', `/doctor/feed/${id}/seen`),
  crew: () => request<CrewMember[]>('GET', '/doctor/crew'),
  approve: (id: number, note: string) => request<FeedItem>('POST', `/doctor/feed/${id}/approve`, { note }),
  approvals: () => request<{ awaiting: FeedItem[]; open_escalations: EscalationItem[] }>('GET', '/doctor/approvals'),
  care: (astronautId: number) => request<CareStatus>('GET', '/care/status', undefined, { astronaut_id: astronautId }),
}
