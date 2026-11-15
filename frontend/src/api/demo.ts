import { request } from './client'

export interface SpaceEvent {
  id: number
  kind: string
  title: string
  summary: string
  severity: 'info' | 'watch' | 'warning' | 'critical'
  astronaut_id: number | null
  astronaut_name: string | null
  status: 'active' | 'resolved'
  reactions: string[]
  refs: { type: string; id?: number }[]
  started_at: string
  ends_at: string | null
  resolved_at: string | null
  triggered_by: string
}

export interface SpaceEvents {
  available: boolean
  auto: boolean
  interval_s: number
  events: SpaceEvent[]
}

export const demoApi = {
  events: (limit = 20) => request<SpaceEvents>('GET', '/demo/events', undefined, { limit }),
  kinds: () => request<{ code: string; title: string }[]>('GET', '/demo/kinds'),
  trigger: (kind?: string) => request<SpaceEvent>('POST', '/demo/events', { kind: kind ?? null }),
  auto: (enabled: boolean) => request<{ auto: boolean }>('POST', '/demo/auto', { enabled }),
}

export const EVENT_COLOR: Record<SpaceEvent['severity'], string> = {
  info: 'var(--color-signal)',
  watch: 'var(--color-sev-watch)',
  warning: 'var(--color-sev-warning)',
  critical: 'var(--color-sev-critical)',
}
