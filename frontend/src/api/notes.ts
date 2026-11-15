import { request } from './client'

export interface AiNote {
  id: number
  astronaut_id: number
  message_id: number | null
  conversation_id: number | null
  category: string
  text: string
  quote: string
  detector: string
  status: string
  is_simulated: boolean
  noted_at: string
}

export interface ValueProposal {
  id: number
  astronaut_id: number
  message_id: number
  metric_code: string
  value: number
  unit: string
  context: string | null
  allowed_contexts: string[]
  quote: string
  status: string
  measurement_id: number | null
  created_at: string
}

export const notesApi = {
  notes: (astronautId: number) => request<AiNote[]>('GET', '/notes', undefined, { astronaut_id: astronautId }),
  values: (astronautId: number) =>
    request<ValueProposal[]>('GET', '/measurement-proposals', undefined, { astronaut_id: astronautId }),
  confirmValue: (id: number, context: string | null) =>
    request<ValueProposal>('POST', `/measurement-proposals/${id}/confirm`, { context }),
  dismissValue: (id: number) => request<ValueProposal>('POST', `/measurement-proposals/${id}/dismiss`),
}
