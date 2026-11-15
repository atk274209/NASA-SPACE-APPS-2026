import { request, type Turn } from './client'

// Hand-written to match backend/am/api/v1/symptoms.py (feature F2). The integrator
// regenerates schema.d.ts; until then these types are the contract.

export type SymptomSeverity = 'mild' | 'moderate' | 'severe'
export const SEVERITIES: SymptomSeverity[] = ['mild', 'moderate', 'severe']

export interface SymptomCatalogItem {
  code: string
  label: string
  category: string
  body_system: string
  body_system_label: string
  report_promptly: boolean
}

export interface SymptomCatalog {
  report_promptly_label: string
  severities: SymptomSeverity[]
  symptoms: SymptomCatalogItem[]
}

export interface SymptomReport {
  id: number
  astronaut_id: number
  symptom_code: string
  label: string
  category: string
  body_system: string
  report_promptly: boolean
  severity: SymptomSeverity
  note: string
  onset_at: string | null
  reported_at: string
  source: 'manual' | 'chat_confirmed' | 'checkin' | 'simulated'
  proposal_id: number | null
  status: 'active' | 'resolved'
  resolved_at: string | null
  is_simulated: boolean
}

export interface SymptomProposal {
  id: number
  astronaut_id: number
  message_id: number
  conversation_id: number | null
  symptom_code: string
  label: string
  report_promptly: boolean
  quote: string
  detector: 'keyword' | 'llm'
  status: 'pending' | 'confirmed' | 'dismissed'
  decided_at: string | null
  created_at: string
}

export interface ConfirmResult {
  proposal: SymptomProposal
  report: SymptomReport
}

export interface NewSymptomReport {
  astronaut_id: number
  symptom_code: string
  severity: SymptomSeverity
  note?: string
  onset_at?: string | null
}

/** A chat turn plus the symptoms AMC noticed in the user's message (pending until confirmed). */
export type TurnWithProposals = Turn & { proposals: SymptomProposal[] }

export const symptomsApi = {
  catalog: () => request<SymptomCatalog>('GET', '/symptoms/catalog'),
  reports: (astronaut_id: number, status?: 'active' | 'resolved') =>
    request<SymptomReport[]>('GET', '/symptoms', undefined, { astronaut_id, status }),
  report: (body: NewSymptomReport) => request<SymptomReport>('POST', '/symptoms', body),
  resolve: (id: number, note: string) => request<SymptomReport>('POST', `/symptoms/${id}/resolve`, { note }),
  proposals: (astronaut_id: number, status: 'pending' | 'confirmed' | 'dismissed' | 'all' = 'pending') =>
    request<SymptomProposal[]>('GET', '/symptoms/proposals', undefined, { astronaut_id, status }),
  confirm: (id: number, severity: SymptomSeverity, note = '') =>
    request<ConfirmResult>('POST', `/symptoms/proposals/${id}/confirm`, { severity, note }),
  dismiss: (id: number) => request<SymptomProposal>('POST', `/symptoms/proposals/${id}/dismiss`),
  send: (conversationId: number, content: string) =>
    request<TurnWithProposals>('POST', `/companion/conversations/${conversationId}/messages`, { content }),
}

export const SOURCE_LABEL: Record<SymptomReport['source'], string> = {
  manual: 'reported by you',
  chat_confirmed: 'confirmed from chat',
  checkin: 'from a check-in',
  simulated: 'simulated',
}
