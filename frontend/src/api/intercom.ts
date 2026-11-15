import { request } from './client'

export interface IntercomChannel {
  id: string
  title: string
  kind: 'crew' | 'private'
  astronaut_id: number | null
  unread: number
  last_body: string | null
  last_sender: string | null
  last_at: string | null
}

export interface IntercomMessage {
  id: number
  channel: string
  sender_user_id: number | null
  sender_name: string
  sender_role: 'doctor' | 'astronaut' | 'am'
  body: string
  urgent: boolean
  created_at: string
}

export const intercomApi = {
  channels: () => request<IntercomChannel[]>('GET', '/intercom/channels'),
  messages: (channel: string, afterId = 0) =>
    request<IntercomMessage[]>('GET', `/intercom/channels/${channel}/messages`, undefined, { after_id: afterId }),
  send: (channel: string, body: string, urgent = false) =>
    request<IntercomMessage>('POST', `/intercom/channels/${channel}/messages`, { body, urgent }),
}
