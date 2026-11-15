// Intercom: text loops between the crew, the flight surgeon and AM. The crew loop is shared by
// everyone; each astronaut also has a private loop with the flight surgeon. AM speaks on the crew
// loop for crew-wide events and on a private loop for anything about one person.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { intercomApi, type IntercomChannel, type IntercomMessage } from '../api/intercom'
import { ErrorNote, Loading } from '../components/ui'
import { fmtTime } from '../lib/format'
import { useAuth } from '../state/auth'

const ROLE_LABEL: Record<IntercomMessage['sender_role'], string> = {
  doctor: 'Flight surgeon',
  astronaut: 'Crew',
  am: 'AM',
}

function ChannelList({ channels, current, onPick }: { channels: IntercomChannel[]; current: string; onPick: (id: string) => void }) {
  return (
    <nav aria-label="Intercom loops" className="min-w-0">
      <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
        {channels.map((c) => (
          <li key={c.id} className="shrink-0">
            <button
              type="button"
              onClick={() => onPick(c.id)}
              aria-current={c.id === current ? 'page' : undefined}
              className={`flex w-full min-w-[12rem] items-start gap-2 rounded-md px-3 py-2 text-left transition-colors ${
                c.id === current ? 'bg-deck text-ink shadow-[inset_2px_0_0_var(--color-signal)]' : 'text-ink-dim hover:bg-deck/60 hover:text-ink'}`}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{c.kind === 'crew' ? 'Crew loop' : c.title}</span>
                <span className="block truncate text-xs text-ink-faint">
                  {c.last_body ? `${c.last_sender}: ${c.last_body}` : c.kind === 'crew' ? 'Everyone on the mission' : 'Only the two of you'}
                </span>
              </span>
              {c.unread > 0 && (
                <span className="num mt-0.5 rounded-sm bg-signal/20 px-1.5 text-xs text-signal" aria-label={`${c.unread} unread`}>{c.unread}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function Thread({ channel, myId, isDoctor }: { channel: IntercomChannel; myId: number; isDoctor: boolean }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [urgent, setUrgent] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  const q = useQuery({
    queryKey: ['intercom', 'messages', channel.id],
    queryFn: () => intercomApi.messages(channel.id),
    refetchInterval: 2_500,
  })
  const send = useMutation({
    mutationFn: () => intercomApi.send(channel.id, text.trim(), urgent),
    onSuccess: () => {
      setText('')
      setUrgent(false)
      qc.invalidateQueries({ queryKey: ['intercom'] })
    },
  })
  const count = q.data?.length ?? 0
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'nearest' })
    if (count) qc.invalidateQueries({ queryKey: ['intercom', 'channels'] }) // reading clears the unread badge
  }, [count, qc])
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (text.trim() && !send.isPending) send.mutate()
  }
  return (
    <section aria-label={channel.kind === 'crew' ? 'Crew loop' : channel.title} className="grid min-w-0 gap-4">
      <header>
        <h2 className="text-lg font-semibold">{channel.kind === 'crew' ? 'Crew loop' : channel.title}</h2>
        <p className="text-sm text-ink-dim">
          {channel.kind === 'crew'
            ? 'Everyone on the mission hears this loop. AM announces crew-wide events here.'
            : 'Only this astronaut and the flight surgeon hear this loop. AM posts here when something concerns only them.'}
        </p>
      </header>
      {q.error && <ErrorNote error={q.error} />}
      {!q.data ? <Loading /> : (
        <ol className="grid max-h-[55vh] gap-3 overflow-y-auto pr-1" aria-live="polite">
          {q.data.map((m) => {
            const mine = m.sender_user_id === myId
            return (
              <li key={m.id} className={mine ? 'justify-self-end' : 'justify-self-start'}>
                <div className={`max-w-[62ch] rounded-lg px-3.5 py-2 ${
                  mine ? 'rounded-br-sm bg-deck' : m.sender_role === 'am' ? 'border border-signal/40 bg-hull' : 'rounded-bl-sm border border-seam bg-hull'
                } ${m.urgent ? 'shadow-[inset_3px_0_0_var(--color-sev-warning)]' : ''}`}>
                  <p className="mb-0.5 flex flex-wrap items-baseline gap-x-2 text-xs text-ink-faint">
                    <span className={`font-medium ${m.sender_role === 'am' ? 'text-signal' : 'text-ink-dim'}`}>
                      {mine ? 'You' : m.sender_name}
                    </span>
                    {!mine && m.sender_role !== 'am' && <span>{ROLE_LABEL[m.sender_role]}</span>}
                    <span className="num">{fmtTime(m.created_at)}</span>
                    {m.urgent && <span className="font-medium text-sev-warning">Urgent</span>}
                  </p>
                  <p className="whitespace-pre-line text-ink">{m.body}</p>
                </div>
              </li>
            )
          })}
          {!q.data.length && <li className="text-sm text-ink-faint">No messages on this loop yet.</li>}
          <div ref={end} />
        </ol>
      )}
      <form onSubmit={submit} className="grid gap-2">
        <label className="sr-only" htmlFor="intercom-input">Message on this loop</label>
        <textarea id="intercom-input" rows={2} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) submit(e) }}
          placeholder={channel.kind === 'crew' ? 'Speak to the whole crew…' : 'Speak on the private loop…'}
          className="w-full resize-y rounded-md border border-seam-strong bg-deck px-3 py-2 text-ink placeholder:text-ink-faint focus:border-signal focus:outline-none" />
        <div className="flex flex-wrap items-center gap-3">
          {isDoctor && (
            <label className="inline-flex items-center gap-2 text-sm text-ink-dim">
              <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} className="size-4 accent-[var(--color-sev-warning)]" />
              Urgent
            </label>
          )}
          <button className="ml-auto h-9 rounded-md bg-ink px-4 text-sm font-semibold text-void disabled:opacity-50"
            disabled={!text.trim() || send.isPending}>Send</button>
        </div>
        {send.error && <ErrorNote error={send.error} />}
      </form>
    </section>
  )
}

export function IntercomPage() {
  const { user, isDoctor } = useAuth()
  const channels = useQuery({ queryKey: ['intercom', 'channels'], queryFn: intercomApi.channels, refetchInterval: 4_000 })
  const [picked, setPicked] = useState<string | null>(null)
  const list = channels.data ?? []
  const current = list.find((c) => c.id === picked) ?? list.find((c) => c.kind === 'private' && !isDoctor) ?? list[0]
  if (channels.error) return <ErrorNote error={channels.error} />
  if (!current) return <Loading />
  return (
    <div className="grid gap-6">
      <header className="grid gap-2">
        <h1 className="text-[1.75rem]">Intercom</h1>
        <p className="max-w-[68ch] text-ink-dim">
          Talk on the station's loops. Messages arrive on every screen within a few seconds; nothing here goes into health records.
        </p>
      </header>
      <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <ChannelList channels={list} current={current.id} onPick={setPicked} />
        <Thread key={current.id} channel={current} myId={user.id} isDoctor={isDoctor} />
      </div>
    </div>
  )
}

/** Unread messages across all of this user's loops, for the navigation badge. */
export function useIntercomUnread(): number {
  const q = useQuery({ queryKey: ['intercom', 'channels'], queryFn: intercomApi.channels, refetchInterval: 4_000 })
  return (q.data ?? []).reduce((n, c) => n + c.unread, 0)
}
