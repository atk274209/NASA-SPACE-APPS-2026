import { request } from './client'

export interface Control {
  id: number
  zone_id: number
  zone_code: string
  zone_name: string
  astronaut_id: number | null
  variable: 'temperature' | 'lighting' | 'pressure'
  unit: string
  mode: 'AUTONOMOUS' | 'PAUSED' | 'MANUAL'
  mode_reason: string
  setpoint: number
  reading: number | null
  read_at: string | null
  fault: string
  drift: number
  min: number
  max: number
  auto_min: number
  auto_max: number
  step: number
}
export interface CabinAction {
  id: number
  zone_code: string
  astronaut_id: number | null
  variable: string
  kind: string
  before: number | null
  after: number | null
  rule_id: string
  actor: string
  reason: string
  verified: boolean | null
  feedback: string | null
  signal_quote: string | null
  reverted: boolean
  created_at: string
}
export interface EnvAlert {
  id: number
  zone_code: string
  variable: string
  severity: string
  rule_id: string
  text: string
  status: string
  acknowledged_by: string | null
  created_at: string
}
export interface CabinState { label: string; controls: Control[]; actions: CabinAction[]; alerts: EnvAlert[] }

export const cabinApi = {
  state: (astronautId: number) => request<CabinState>('GET', '/cabin/state', undefined, { astronaut_id: astronautId }),
  overview: () => request<CabinState>('GET', '/cabin/overview'),
  set: (controlId: number, value: number, reason = '') => request<CabinAction>('POST', `/cabin/controls/${controlId}/set`, { value, reason }),
  mode: (controlId: number, mode: Control['mode'], reason = '') => request<CabinAction>('POST', `/cabin/controls/${controlId}/mode`, { mode, reason }),
  pauseAll: (mode: Control['mode'], reason: string) => request<CabinAction[]>('POST', '/cabin/pause-all', { mode, reason }),
  revert: (actionId: number) => request<CabinAction>('POST', `/cabin/actions/${actionId}/revert`),
  feedback: (actionId: number, helped: boolean) => request<CabinAction>('POST', `/cabin/actions/${actionId}/feedback`, { helped }),
  ackAlert: (id: number) => request<EnvAlert>('POST', `/cabin/alerts/${id}/acknowledge`),
}

export const VAR_LABEL: Record<string, string> = { temperature: 'Temperature', lighting: 'Lighting', pressure: 'Cabin pressure' }
