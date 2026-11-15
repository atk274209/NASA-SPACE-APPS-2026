import { request } from './client'

export type DiaryLevel = 'none' | 'green' | 'yellow' | 'red'

export interface DiaryDay {
  id: number
  day_date: string
  mission_day: number | null
  level: DiaryLevel
  rule_level: DiaryLevel
  rule_reasons: string[]
  ai_status: 'none' | 'done' | 'unavailable' | 'rejected'
  ai_level: DiaryLevel | null
  ai_reason: string
  ai_quote: string
  summary: string
  analysed_at: string | null
  is_today: boolean
}

export interface DiaryMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  generated_by: string
  created_at: string
}

export interface DiaryForward {
  id: number
  message_id: number
  symptom_code: string
  label: string
  quote: string
  status: 'sent_urgent' | 'offered' | 'sent' | 'kept_private'
}

export interface DiaryPage {
  day: DiaryDay
  messages: DiaryMessage[]
  forwards: DiaryForward[]
  ai_available: boolean
}

export const diaryApi = {
  today: () => request<DiaryPage>('GET', '/diary/today'),
  days: () => request<DiaryDay[]>('GET', '/diary/days'),
  page: (id: number) => request<DiaryPage>('GET', `/diary/days/${id}`),
  write: (id: number, content: string) => request<DiaryPage>('POST', `/diary/days/${id}/messages`, { content }),
  analyse: (id: number) => request<DiaryDay>('POST', `/diary/days/${id}/analyse`),
  decide: (id: number, send: boolean, severity?: string) =>
    request<DiaryForward>('POST', `/diary/forwards/${id}`, { send, severity }),
}
