import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ChatMessage, type CheckinItem } from '../api/client'
import { Button, Empty, ErrorNote, inputCls, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtAgo, fmtDateTime, fmtTime } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

type Ref = { type: string; id?: number; unverified_numbers?: number[] }

function Sources({ msg }: { msg: ChatMessage }) {
  const refs = msg.refs as Ref[]
  const count = (t: string) => refs.filter((r) => r.type === t).length
  const alertIds = refs.filter((r) => r.type === 'alert').map((r) => r.id!)
  const grounding = refs.find((r) => r.type === 'grounding_check')
  const tools = (msg.tool_calls as { name: string; ok: boolean }[]) ?? []
  const parts = [
    count('measurement') && `${count('measurement')} measurements`,
    count('baseline') && `${count('baseline')} baselines`,
    count('self_report') && `${count('self_report')} check-in ratings`,
  ].filter(Boolean)
  return (
    <div className="mt-2 space-y-1 text-xs text-ink-dim">
      {msg.generated_by === 'deterministic' && (
        <p className="text-sev-watch">Written by AM&apos;s rule-based summary because the language model is unavailable.</p>
      )}
      {tools.length > 0 && (
        <p>Checked: {tools.map((t) => t.name.replace('get_', '').replaceAll('_', ' ')).join(', ')}</p>
      )}
      {(parts.length > 0 || alertIds.length > 0) && (
        <p>
          Based on {parts.join(', ')}
          {alertIds.length > 0 && (
            <>
              {parts.length ? ' and ' : ''}alerts{' '}
              {alertIds.map((id, i) => (
                <span key={id}>
                  {i > 0 && ', '}
                  <Link to={`/alerts?id=${id}&filter=all`} className="num text-signal hover:underline">#{id}</Link>
                </span>
              ))}
            </>
          )}
        </p>
      )}
      {grounding?.unverified_numbers?.length ? (
        <p className="text-sev-warning">
          Check before relying on this answer: {grounding.unverified_numbers.join(', ')}{' '}
          {grounding.unverified_numbers.length === 1 ? 'does' : 'do'} not appear in the records AM retrieved.
        </p>
      ) : null}
      {msg.model && (
        <p className="text-ink-faint">
          {msg.model}
          {msg.latency_ms != null && `, ${(msg.latency_ms / 1000).toFixed(1)} s`}
        </p>
      )}
    </div>
  )
}

function Bubble({ msg }: { msg: ChatMessage }) {
  const mine = msg.role === 'user'
  return (
    <motion.li
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`flex ${mine ? 'justify-end' : 'justify-start'}`}
    >
      <div className={`max-w-[min(68ch,85%)] ${mine ? 'rounded-lg rounded-br-sm bg-deck px-3.5 py-2.5' : 'border-l-2 border-signal/60 pl-3.5'}`}>
        <p className="mb-0.5 text-xs text-ink-faint">
          {mine ? 'You' : 'AM'}, {fmtTime(msg.created_at)}
        </p>
        <div className="whitespace-pre-wrap leading-relaxed text-ink">{msg.content}</div>
        {!mine && <Sources msg={msg} />}
      </div>
    </motion.li>
  )
}

function Chat({ conversationId, astronautId }: { conversationId: number; astronautId: number }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLOListElement>(null)
  const messages = useQuery({ queryKey: ['messages', conversationId], queryFn: () => api.messages(conversationId) })
  const suggestions = useQuery({ queryKey: ['suggestions', astronautId], queryFn: () => api.suggestions(astronautId) })
  const send = useMutation({
    mutationFn: (text: string) => api.send(conversationId, text),
    onMutate: () => setDraft(''),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['messages', conversationId] })
      qc.invalidateQueries({ queryKey: ['conversations', astronautId] })
    },
  })
  const pendingText = send.isPending ? send.variables : null

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages.data?.length, pendingText])

  const submit = (text: string) => {
    const t = text.trim()
    if (t && !send.isPending) send.mutate(t)
  }
  const msgs = messages.data ?? []

  return (
    <div className="flex min-h-[60vh] flex-col">
      <ol ref={listRef} aria-live="polite" className="flex flex-1 flex-col gap-5 overflow-y-auto pb-4">
        {msgs.length === 0 && !pendingText && (
          <li className="py-6">
            <p className="text-ink-dim">Ask about your readings, alerts, baselines or recent check-ins.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(suggestions.data ?? []).map((s) => (
                <button
                  key={s.text}
                  title={s.reason}
                  onClick={() => submit(s.text)}
                  className="rounded-md border border-seam-strong px-3 py-1.5 text-left text-sm text-ink hover:border-signal"
                >
                  {s.text}
                </button>
              ))}
            </div>
          </li>
        )}
        {msgs.map((m) => <Bubble key={m.id} msg={m} />)}
        {pendingText && (
          <>
            <li className="flex justify-end">
              <div className="max-w-[min(68ch,85%)] rounded-lg rounded-br-sm bg-deck px-3.5 py-2.5 text-ink">{pendingText}</div>
            </li>
            <li className="border-l-2 border-signal/30 pl-3.5 text-sm text-ink-dim">
              <motion.span animate={{ opacity: [0.4, 1, 0.4] }} transition={{ repeat: Infinity, duration: 1.6 }}>
                AM is checking the records…
              </motion.span>
            </li>
          </>
        )}
      </ol>
      {send.error && <ErrorNote error={send.error} />}
      <form
        className="sticky bottom-0 mt-2 flex gap-2 border-t border-seam bg-void pt-3"
        onSubmit={(e) => { e.preventDefault(); submit(draft) }}
      >
        <label htmlFor="msg" className="sr-only">Message</label>
        <textarea
          id="msg"
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(draft) } }}
          placeholder="Message AM (Enter to send, Shift+Enter for a new line)"
          className={`${inputCls} flex-1 resize-none`}
        />
        <Button type="submit" disabled={!draft.trim() || send.isPending} className="self-end">Send</Button>
      </form>
    </div>
  )
}

function Scale({ item, value, onChange }: { item: CheckinItem; value: number | undefined; onChange: (v: number) => void }) {
  return (
    <fieldset>
      <legend className="text-sm text-ink">{item.label}</legend>
      <div className="mt-1 flex items-center gap-2 text-xs text-ink-faint">
        <span className="w-16 text-right">{item.low_anchor}</span>
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((n) => (
            <label key={n} className="cursor-pointer">
              <input type="radio" name={item.key} value={n} checked={value === n} onChange={() => onChange(n)} className="peer sr-only" />
              <span className="num grid size-8 place-items-center rounded-md border border-seam-strong text-sm text-ink-dim peer-checked:border-signal peer-checked:bg-signal/15 peer-checked:text-ink peer-focus-visible:outline-2 peer-focus-visible:outline-signal">
                {n}
              </span>
            </label>
          ))}
        </div>
        <span className="w-16">{item.high_anchor}</span>
      </div>
    </fieldset>
  )
}

function CheckinPanel({ astronautId }: { astronautId: number }) {
  const qc = useQueryClient()
  const items = useQuery({ queryKey: ['checkin-items'], queryFn: api.checkinItems })
  const [ratings, setRatings] = useState<Record<string, number>>({})
  const [note, setNote] = useState('')
  const submit = useMutation({
    mutationFn: () => api.submitCheckin(astronautId, ratings, note),
    onSuccess: () => {
      setRatings({})
      setNote('')
      qc.invalidateQueries({ queryKey: ['memory', astronautId] })
      qc.invalidateQueries({ queryKey: ['suggestions', astronautId] })
    },
  })
  return (
    <Section title="Daily check-in">
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); submit.mutate() }}>
        {items.data?.map((it) => (
          <Scale key={it.key} item={it} value={ratings[it.key]} onChange={(v) => setRatings((r) => ({ ...r, [it.key]: v }))} />
        ))}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink">Anything else to note</span>
          <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} className={`${inputCls} resize-none`} />
        </label>
        <Button type="submit" disabled={submit.isPending || (!Object.keys(ratings).length && !note.trim())}>
          Save check-in
        </Button>
        {submit.isSuccess && <p role="status" className="text-sm text-sev-normal">Check-in saved. AM will use it today.</p>}
        {submit.error && <ErrorNote error={submit.error} />}
        <p className="text-xs text-ink-faint">Self-ratings from 1 to 5. They are not a clinical assessment.</p>
      </form>
    </Section>
  )
}

function MemoryPanel({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['memory', astronautId], queryFn: () => api.memory(astronautId), refetchInterval: 30_000 })
  const items = q.data ?? []
  const short = items.filter((m) => m.layer === 'short')
  const mission = items.filter((m) => m.layer === 'mission').slice(0, 8)
  const long = items.filter((m) => m.layer === 'long')
  return (
    <Section title="What AM remembers">
      {q.isLoading ? <Loading /> : (
        <div className="flex flex-col gap-4 text-sm">
          <div>
            <h3 className="text-ink-dim">Today, from you</h3>
            {short.length ? (
              <ul className="mt-1 space-y-1">{short.map((m) => <li key={m.id}>{m.content} <span className="text-xs text-ink-faint">{fmtAgo(m.occurred_at)}</span></li>)}</ul>
            ) : <p className="mt-1 text-ink-faint">Nothing yet. A check-in adds to it; it clears after 24 hours.</p>}
          </div>
          <div>
            <h3 className="text-ink-dim">Mission events</h3>
            {mission.length ? (
              <ul className="mt-1 space-y-1.5">
                {mission.map((m) => (
                  <li key={m.id}>
                    <span className="num text-xs text-ink-faint">{fmtDateTime(m.occurred_at)}</span>{' '}
                    {m.content}
                  </li>
                ))}
              </ul>
            ) : <p className="mt-1 text-ink-faint">No significant events recorded.</p>}
          </div>
          <div>
            <h3 className="text-ink-dim">Long-term patterns</h3>
            <p className="mt-1 text-ink-faint">
              {long.length ? `${long.length} notes` : 'Filled by the mission journal once daily summaries are available.'}
            </p>
          </div>
        </div>
      )}
    </Section>
  )
}

export function CompanionPage() {
  const { astronaut } = useSession()
  const qc = useQueryClient()
  const [active, setActive] = useState<number | null>(null)
  const ai = useQuery({ queryKey: ['ai-status'], queryFn: api.aiStatus, refetchInterval: 20_000, retry: 0 })
  const convs = useQuery({
    queryKey: ['conversations', astronaut?.id],
    queryFn: () => api.conversations(astronaut!.id),
    enabled: !!astronaut,
  })
  const create = useMutation({
    mutationFn: () => api.createConversation(astronaut!.id),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ['conversations', astronaut?.id] })
      setActive(c.id)
    },
  })

  useEffect(() => setActive(null), [astronaut?.id])

  if (!astronaut) return <NoCrew />
  const list = convs.data ?? []
  const current = active ?? list[0]?.id ?? null
  const aiReady = ai.data?.reachable && ai.data.model_installed

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-2xl font-semibold">AM companion</h1>
        {astronaut.is_simulated && <SimulatedMark />}
        <span className="text-sm text-ink-dim">
          {ai.isLoading ? 'Checking model…' : aiReady
            ? <>Local model <span className="num text-ink">{ai.data!.model}</span></>
            : <span className="text-sev-watch">
                {ai.data?.reachable ? `Model ${ai.data.model} not installed` : 'Language model offline'}: answers fall back to rule-based summaries
              </span>}
        </span>
      </div>

      <div className="grid gap-8 xl:grid-cols-[220px_minmax(0,1fr)_340px]">
        <nav aria-label="Conversations" className="flex flex-col gap-2">
          <Button variant="quiet" onClick={() => create.mutate()} disabled={create.isPending}>New conversation</Button>
          <ul className="flex flex-col">
            {list.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => setActive(c.id)}
                  aria-current={c.id === current}
                  className={`w-full truncate rounded-md px-2.5 py-2 text-left text-sm ${c.id === current ? 'bg-deck text-ink' : 'text-ink-dim hover:bg-deck/50'}`}
                >
                  {c.title}
                  <span className="block text-xs text-ink-faint">{fmtAgo(c.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0">
          <AnimatePresence mode="wait">
            {current == null ? (
              <Empty title="No conversations yet">
                <Button className="mt-3" onClick={() => create.mutate()} disabled={create.isPending}>Start a conversation</Button>
              </Empty>
            ) : (
              <motion.div key={current} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
                <Chat conversationId={current} astronautId={astronaut.id} />
              </motion.div>
            )}
          </AnimatePresence>
          {create.error && <ErrorNote error={create.error} />}
        </div>

        <aside className="flex flex-col gap-8">
          <CheckinPanel astronautId={astronaut.id} />
          <MemoryPanel astronautId={astronaut.id} />
        </aside>
      </div>
    </div>
  )
}
