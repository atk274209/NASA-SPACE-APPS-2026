import { request } from './client'

export interface DutyAssignment {
  id: number
  task_code: string
  title: string
  detail: string
  kind: 'physical' | 'focus' | 'social' | 'restorative'
  load: number
  minutes: number
  reason: string
  status: 'planned' | 'done'
}

export interface DutyMember {
  astronaut_id: number
  code: string
  name: string
  energy: number | null
  morale: number | null
  band: 'full' | 'steady' | 'light'
  capacity: number
  reasons: string[]
  note: string
  note_by: 'llm' | 'template'
  assignments: DutyAssignment[]
}

export interface DutyRoster {
  plan_date: string
  created_at: string
  created_by: string
  label: string
  postponed: { code: string; title: string; load: number }[]
  crew: DutyMember[]
}

export const dutiesApi = {
  today: () => request<DutyRoster>('GET', '/duties/today'),
  replan: () => request<DutyRoster>('POST', '/duties/today/replan'),
  toggle: (id: number) => request<DutyAssignment>('POST', `/duties/assignments/${id}/done`),
}

export const BAND_LABEL: Record<DutyMember['band'], string> = {
  full: 'Full day',
  steady: 'Steady day',
  light: 'Light day',
}

export const KIND_LABEL: Record<DutyAssignment['kind'], string> = {
  physical: 'Hands-on',
  focus: 'Focus',
  social: 'Social',
  restorative: 'Restorative',
}
