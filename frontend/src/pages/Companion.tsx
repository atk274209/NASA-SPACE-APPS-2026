import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { Fragment, useEffect, useId, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ChatMessage, type CheckinItem } from '../api/client'
import {
  SEVERITIES,
  SOURCE_LABEL,
  symptomsApi,
  type SymptomProposal,
  type SymptomSeverity,
} from '../api/symptoms'
import { Button, Empty, ErrorNote, inputCls, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtAgo, fmtDateTime, fmtTime, titleCase } from '../lib/format'
import { useAuth } from '../state/auth'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'
import { CareBanner } from '../components/CareBanner'
import { NotesList, ValueProposals } from '../components/Capture'
import { PointsBadge, RoutineExams } from '../components/Exams'
import { ComfortStrip } from '../components/ComfortStrip'
import { HydrationCard, HydrationLeaderboard, StreakChips } from '../components/Hydration'

type Ref = { type: string; id?: number; unverified_numbers?: number[]; doc_slug?: string; title?: string; url?: string }

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function Sources({ msg }: { msg: ChatMessage }) {
  const refs = msg.refs as Ref[]
  const count = (t: string) => refs.filter((r) => r.type === t).length
  const alertIds = refs.filter((r) => r.type === 'alert').map((r) => r.id!)
  const grounding = refs.find((r) => r.type === 'grounding_check')
  const tools = (msg.tool_calls as { name: string; ok: boolean }[]) ?? []
  const docs = [...new Map(refs.filter((r) => r.type === 'knowledge' && r.url).map((r) => [r.doc_slug, r])).values()]
  const ruleAnswer = refs.some((r) => r.type === 'safety_rule')
  const urgent = refs.some((r) => r.type === 'safety_rule' && (r as { rule?: string }).rule === 'report_promptly_contact_now')
  const opener = refs.some((r) => r.type === 'persona')
  const wording = refs.find((r) => r.type === 'wording_check') as { flags?: string[] } | undefined
  const { isDoctor } = useAuth()
  const parts = [
    count('measurement') && `${count('measurement')} measurements`,
    count('baseline') && `${count('baseline')} baselines`,
    count('self_report') && `${count('self_report')} check-in ratings`,
    count('symptom_report') && plural(count('symptom_report'), 'symptom report'),
  ].filter(Boolean)
  return (
    <div className="mt-2 space-y-1 text-xs text-ink-dim">
      {msg.generated_by === 'deterministic' && !opener && (urgent ? (
        <p>Fixed safety answer: symptoms on the DEMO report-promptly list always get these words, not the language model.</p>
      ) : ruleAnswer ? (
        <p>Fixed answer from AM&apos;s rules: questions about treating a symptom are not sent to the language model.</p>
      ) : (
        <p className="text-sev-watch">Written by AM&apos;s rule-based summary because the language model is unavailable.</p>
      ))}
      {wording?.flags?.length ? (
        <p className="text-sev-watch">Wording check: this AI answer uses words that need care ({wording.flags.join(', ')}). It is not medical advice.</p>
      ) : null}
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
                  <Link to={isDoctor ? `/alerts?id=${id}&filter=all` : '/health'} className="num text-signal hover:underline">#{id}</Link>
                </span>
              ))}
            </>
          )}
        </p>
      )}
      {docs.length > 0 && (
        <p>
          Background from{' '}
          {docs.map((d, i) => (
            <span key={d.doc_slug}>
              {i > 0 && '; '}
              <a href={d.url} target="_blank" rel="noreferrer" className="text-signal hover:underline">{d.title}</a>
            </span>
          ))}
          . General research, not your own data.
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

// ------------------------------------------------------------------ symptoms
function useSymptomCatalog() {
  return useQuery({ queryKey: ['symptom-catalog'], queryFn: symptomsApi.catalog, staleTime: Infinity })
}

/** Everything a symptom decision can change: reports, proposals, memory notes and today's missions. */
function useSymptomRefresh(astronautId: number) {
  const qc = useQueryClient()
  return () => {
    for (const key of ['symptoms', 'proposals', 'memory', 'today']) qc.invalidateQueries({ queryKey: [key, astronautId] })
  }
}

function PromptlyTag() {
  const catalog = useSymptomCatalog()
  return (
    <span title={catalog.data?.report_promptly_label} className="text-xs text-sev-warning">
      Report promptly (DEMO list)
    </span>
  )
}

function SeverityPicker({ name, value, onChange }: {
  name: string
  value: SymptomSeverity | null
  onChange: (s: SymptomSeverity) => void
}) {
  return (
    <fieldset className="flex flex-wrap items-center gap-1.5">
      <legend className="mb-1 text-xs text-ink-dim">How strong is it? Your own rating</legend>
      {SEVERITIES.map((s) => (
        <label key={s} className="cursor-pointer">
          <input type="radio" name={name} value={s} checked={value === s} onChange={() => onChange(s)} className="peer sr-only" />
          <span className="inline-block rounded-md border border-seam-strong px-2.5 py-1 text-sm text-ink-dim peer-checked:border-signal peer-checked:bg-signal/15 peer-checked:text-ink peer-focus-visible:outline-2 peer-focus-visible:outline-signal">
            {titleCase(s)}
          </span>
        </label>
      ))}
    </fieldset>
  )
}

function ProposalActions({ p, astronautId }: { p: SymptomProposal; astronautId: number }) {
  const refresh = useSymptomRefresh(astronautId)
  const [severity, setSeverity] = useState<SymptomSeverity | null>(null)
  const confirm = useMutation({ mutationFn: (s: SymptomSeverity) => symptomsApi.confirm(p.id, s), onSuccess: refresh })
  const dismiss = useMutation({ mutationFn: () => symptomsApi.dismiss(p.id), onSuccess: refresh })
  const busy = confirm.isPending || dismiss.isPending
  const error = confirm.error ?? dismiss.error
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium text-ink">{p.label}</span>
        {p.report_promptly && <PromptlyTag />}
      </p>
      <blockquote className="border-l-2 border-seam-strong pl-2.5 text-ink-dim">“{p.quote}”</blockquote>
      <SeverityPicker name={`proposal-${p.id}`} value={severity} onChange={setSeverity} />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => severity && confirm.mutate(severity)} disabled={!severity || busy}>Confirm symptom</Button>
        <Button variant="quiet" onClick={() => dismiss.mutate()} disabled={busy}>Dismiss</Button>
      </div>
      {error && <ErrorNote error={error} />}
    </div>
  )
}

/** Shown under the user's own message: what AM noticed in it, and what was decided. */
function NoticedInline({ proposals, astronautId }: { proposals: SymptomProposal[]; astronautId: number }) {
  const pending = proposals.filter((p) => p.status === 'pending')
  const decided = proposals.filter((p) => p.status !== 'pending')
  return (
    <li className="flex justify-end">
      <div className="flex w-full max-w-[min(68ch,85%)] flex-col gap-3">
        {pending.length > 0 && (
          <div className="flex flex-col gap-4 rounded-md border border-sev-watch/40 px-3.5 py-3">
            <p className="text-xs text-sev-watch">
              AM noticed a possible symptom in this message. Nothing is recorded until you confirm it.
            </p>
            {pending.map((p) => <ProposalActions key={p.id} p={p} astronautId={astronautId} />)}
          </div>
        )}
        {decided.map((p) => (
          <p key={p.id} className="text-right text-xs text-ink-faint">
            {p.label}: {p.status === 'confirmed' ? 'confirmed and recorded' : 'dismissed'}
          </p>
        ))}
      </div>
    </li>
  )
}

function NoticedPanel({ astronautId, onOpenConversation }: { astronautId: number; onOpenConversation: (id: number) => void }) {
  const q = useQuery({ queryKey: ['proposals', astronautId], queryFn: () => symptomsApi.proposals(astronautId, 'all') })
  const pending = (q.data ?? []).filter((p) => p.status === 'pending')
  return (
    <Section title="Possible symptoms noticed" aside={pending.length ? pending.length : undefined}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorNote error={q.error} /> : pending.length === 0 ? (
        <p className="text-sm text-ink-faint">
          Nothing waiting. If you mention a symptom in chat, AM asks you here before anything is recorded.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-seam/60">
          {pending.map((p) => (
            <li key={p.id} className="py-3 first:pt-0">
              <ProposalActions p={p} astronautId={astronautId} />
              <p className="mt-1.5 text-xs text-ink-faint">
                {p.detector === 'llm' ? 'Noticed by the local model' : 'Noticed from keywords'}, {fmtAgo(p.created_at)}
                {p.conversation_id != null && (
                  <>
                    {', '}
                    <button className="text-signal hover:underline" onClick={() => onOpenConversation(p.conversation_id!)}>
                      show in conversation
                    </button>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function ResolveControl({ id, label, astronautId }: { id: number; label: string; astronautId: number }) {
  const refresh = useSymptomRefresh(astronautId)
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  const resolve = useMutation({ mutationFn: () => symptomsApi.resolve(id, note), onSuccess: refresh })
  if (!open) {
    return <Button variant="quiet" onClick={() => setOpen(true)}>Resolve</Button>
  }
  return (
    <form className="mt-2 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); resolve.mutate() }}>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-ink-dim">Note for the medical team (optional)</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} className={inputCls} />
      </label>
      <div className="flex gap-2">
        <Button type="submit" disabled={resolve.isPending}>Mark {label.toLowerCase()} resolved</Button>
        <Button type="button" variant="quiet" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
      {resolve.error && <ErrorNote error={resolve.error} />}
    </form>
  )
}

function ActiveSymptomsPanel({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['symptoms', astronautId], queryFn: () => symptomsApi.reports(astronautId) })
  const active = (q.data ?? []).filter((r) => r.status === 'active')
  return (
    <Section title="Active symptoms" aside={active.length ? active.length : undefined}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorNote error={q.error} /> : active.length === 0 ? (
        <p className="text-sm text-ink-faint">No active symptoms.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-seam/60 text-sm">
          {active.map((r) => (
            <li key={r.id} className="py-2.5 first:pt-0">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-ink">{r.label}</span>
                  <span className="text-ink-dim">{r.severity}</span>
                  {r.report_promptly && <PromptlyTag />}
                </p>
                <ResolveControl id={r.id} label={r.label} astronautId={astronautId} />
              </div>
              <p className="text-xs text-ink-faint">
                {titleCase(SOURCE_LABEL[r.source] ?? r.source)}, {fmtDateTime(r.reported_at)}
                {r.onset_at && `, started ${fmtDateTime(r.onset_at)}`}
              </p>
              {r.note && <p className="mt-0.5 text-ink-dim">{r.note}</p>}
            </li>
          ))}
        </ul>
      )}
      {active.length > 0 && (
        <p className="mt-2 text-xs text-ink-faint">
          Each active symptom adds follow-up prompts to your <Link to="/missions" className="text-signal hover:underline">daily missions</Link>.
        </p>
      )}
    </Section>
  )
}

/** Local datetime-local value for "now", used as the latest selectable onset time. */
function localNow(): string {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function ReportSymptomPanel({ astronautId }: { astronautId: number }) {
  const refresh = useSymptomRefresh(astronautId)
  const catalog = useSymptomCatalog()
  const reports = useQuery({ queryKey: ['symptoms', astronautId], queryFn: () => symptomsApi.reports(astronautId) })
  const listId = useId()
  const [search, setSearch] = useState('')
  const [code, setCode] = useState<string | null>(null)
  const [severity, setSeverity] = useState<SymptomSeverity | null>(null)
  const [onset, setOnset] = useState('')
  const [note, setNote] = useState('')
  const submit = useMutation({
    mutationFn: () => symptomsApi.report({
      astronaut_id: astronautId,
      symptom_code: code!,
      severity: severity!,
      note,
      onset_at: onset ? new Date(onset).toISOString() : null,
    }),
    onSuccess: () => {
      setCode(null)
      setSeverity(null)
      setOnset('')
      setNote('')
      setSearch('')
      refresh()
    },
  })

  const activeCodes = new Set((reports.data ?? []).filter((r) => r.status === 'active').map((r) => r.symptom_code))
  const term = search.trim().toLowerCase()
  const matches = (catalog.data?.symptoms ?? []).filter(
    (s) => !term || s.label.toLowerCase().includes(term) || s.body_system_label.toLowerCase().includes(term),
  )
  const selected = catalog.data?.symptoms.find((s) => s.code === code)

  return (
    <Section title="Report a symptom">
      {catalog.isLoading ? <Loading /> : catalog.error ? <ErrorNote error={catalog.error} /> : (
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (code && severity) submit.mutate() }}>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-ink-dim">Search symptoms</span>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-controls={listId}
              placeholder="For example: headache, sleep, digestion"
              className={inputCls}
            />
          </label>
          <fieldset id={listId} className="max-h-52 overflow-y-auto rounded-md border border-seam">
            <legend className="sr-only">Symptom</legend>
            {matches.length === 0 && <p className="px-3 py-2 text-sm text-ink-faint">No symptom matches “{search}”.</p>}
            {matches.map((s) => (
              <label
                key={s.code}
                className={`flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-sm ${s.code === code ? 'bg-signal/15 text-ink' : 'text-ink-dim hover:bg-deck/60'}`}
              >
                <input type="radio" name="symptom" value={s.code} checked={s.code === code} onChange={() => setCode(s.code)} className="accent-[var(--color-signal)]" />
                <span className="text-ink">{s.label}</span>
                <span className="ml-auto shrink-0 text-xs text-ink-faint">
                  {activeCodes.has(s.code) ? 'active' : s.body_system_label}
                </span>
              </label>
            ))}
          </fieldset>
          {selected?.report_promptly && (
            <p className="text-sm text-sev-warning">
              On the DEMO report-promptly list: tell the crew medical officer or flight surgeon without waiting.
              <span className="mt-0.5 block text-xs text-ink-faint">{catalog.data?.report_promptly_label}</span>
            </p>
          )}
          <SeverityPicker name="report-severity" value={severity} onChange={setSeverity} />
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-ink-dim">When did it start? (optional)</span>
            <input type="datetime-local" value={onset} max={localNow()} onChange={(e) => setOnset(e.target.value)} className={inputCls} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-ink-dim">Note (optional)</span>
            <textarea rows={2} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} className={`${inputCls} resize-none`} />
          </label>
          <Button type="submit" disabled={!code || !severity || submit.isPending}>Report symptom</Button>
          {submit.isSuccess && (
            <p role="status" className="text-sm text-sev-normal">
              Recorded. Follow-up prompts are in your <Link to="/missions" className="underline">daily missions</Link>.
            </p>
          )}
          {submit.error && <ErrorNote error={submit.error} />}
          <p className="text-xs text-ink-faint">
            Your own rating, not a diagnosis. AM adds logging and reporting prompts; it never suggests treatment.
          </p>
        </form>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ chat
function Chat({ conversationId, astronautId }: { conversationId: number; astronautId: number }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLOListElement>(null)
  const messages = useQuery({ queryKey: ['messages', conversationId], queryFn: () => api.messages(conversationId) })
  const suggestions = useQuery({ queryKey: ['suggestions', astronautId], queryFn: () => api.suggestions(astronautId) })
  const proposals = useQuery({ queryKey: ['proposals', astronautId], queryFn: () => symptomsApi.proposals(astronautId, 'all') })
  const send = useMutation({
    mutationFn: (text: string) => symptomsApi.send(conversationId, text),
    onMutate: () => setDraft(''),
    onSuccess: (turn) => {
      // show what AM noticed under the message right away, before the refetch lands
      if (turn.proposals.length) {
        qc.setQueryData<SymptomProposal[]>(['proposals', astronautId], (old = []) => [
          ...turn.proposals,
          ...old.filter((p) => !turn.proposals.some((n) => n.id === p.id)),
        ])
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['messages', conversationId] })
      qc.invalidateQueries({ queryKey: ['conversations', astronautId] })
      qc.invalidateQueries({ queryKey: ['proposals', astronautId] })
    },
  })
  const pendingText = send.isPending ? send.variables : null

  const byMessage = useMemo(() => {
    const m = new Map<number, SymptomProposal[]>()
    for (const p of proposals.data ?? []) m.set(p.message_id, [...(m.get(p.message_id) ?? []), p])
    return m
  }, [proposals.data])

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
            <p className="text-ink-dim">
              Ask about your readings, alerts, baselines or recent check-ins, or tell AM how you feel. If you mention a
              symptom, AM asks you to confirm it before recording anything.
            </p>
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
        {msgs.map((m) => {
          const noticed = m.role === 'user' ? byMessage.get(m.id) : undefined
          return (
            <Fragment key={m.id}>
              <Bubble msg={m} />
              {noticed?.length ? <NoticedInline proposals={noticed} astronautId={astronautId} /> : null}
            </Fragment>
          )
        })}
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
            ) : <p className="mt-1 text-ink-faint">Nothing yet. A check-in or a symptom report adds to it; it clears after 24 hours.</p>}
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
      <CareBanner astronautId={astronaut.id} />
      <ValueProposals astronautId={astronaut.id} />
      <StreakChips astronautId={astronaut.id} />
      <div className="grid gap-3 lg:grid-cols-2">
        <HydrationCard astronautId={astronaut.id} />
        <HydrationLeaderboard />
      </div>
      <RoutineExams astronautId={astronaut.id} compact />
      <ComfortStrip astronautId={astronaut.id} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-2xl font-semibold">AM companion</h1>
        {astronaut.is_simulated && <SimulatedMark />}
        <PointsBadge astronautId={astronaut.id} />
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
          <NoticedPanel astronautId={astronaut.id} onOpenConversation={setActive} />
          <ActiveSymptomsPanel astronautId={astronaut.id} />
          <ReportSymptomPanel astronautId={astronaut.id} />
          <CheckinPanel astronautId={astronaut.id} />
          <MemoryPanel astronautId={astronaut.id} />
        </aside>
      </div>
      <details className="rounded-md border border-seam bg-hull px-4 py-3">
        <summary className="cursor-pointer text-sm font-semibold text-ink">AM&apos;s notes about you</summary>
        <p className="mb-2 mt-1 text-xs text-ink-faint">Only you and the medical officer can read these. Each note shows the words it came from.</p>
        <NotesList astronautId={astronaut.id} />
      </details>
    </div>
  )
}
