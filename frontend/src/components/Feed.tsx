// Doctor's live feed: everything astronauts write, flagged yellow/red.
// The colour is the higher of the deterministic rule level and a validated AI suggestion;
// the AI can raise attention but never lower a rule level (see backend am.triage).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { doctorApi, type FeedItem, type FlagLevel } from '../api/doctor'
import { fmtDateTime } from '../lib/format'
import { Empty, ErrorNote, SimulatedMark } from './ui'

export const LEVEL_COLOR: Record<FlagLevel, string> = {
  red: 'var(--color-sev-critical)',
  yellow: 'var(--color-sev-watch)',
  none: 'var(--color-ink-faint)',
}
const LEVEL_LABEL: Record<FlagLevel, string> = { red: 'Red', yellow: 'Yellow', none: 'No flag' }
const SOURCE_LABEL: Record<FeedItem['source_type'], string> = {
  message: 'Message to AM',
  symptom_report: 'Symptom report',
  checkin_note: 'Check-in note',
  diary: 'From the private diary',
}

export function LevelDot({ level, title }: { level: FlagLevel; title?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: LEVEL_COLOR[level] }} title={title}>
      <span aria-hidden className="size-2.5 rounded-full" style={{ background: LEVEL_COLOR[level] }} />
      {LEVEL_LABEL[level]}
    </span>
  )
}

function AiBox({ f }: { f: FeedItem }) {
  if (f.ai_status === 'pending') return <p className="text-xs text-ink-faint">AI suggestion: waiting for the local model…</p>
  if (f.ai_status === 'unavailable') return <p className="text-xs text-ink-faint">AI suggestion: local model unavailable. Rule flag only.</p>
  if (f.ai_status === 'rejected') return <p className="text-xs text-ink-faint">AI suggestion discarded: it did not quote the astronaut's text.</p>
  if (f.ai_status !== 'done' || !f.ai_level) return null
  return (
    <div className="mt-2 rounded-md border border-seam bg-void/40 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-ink-dim">AI suggestion</span>
        <LevelDot level={f.ai_level} />
        <span className="text-xs text-ink-faint">Local AI. Not a diagnosis; it can only raise the flag.</span>
      </div>
      {f.ai_reason && <p className="mt-1 text-ink-dim">{f.ai_reason}</p>}
      {f.ai_quote && <p className="mt-1 text-ink">“{f.ai_quote}”</p>}
    </div>
  )
}

function Approval({ f }: { f: FeedItem }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const approve = useMutation({
    mutationFn: () => doctorApi.approve(f.id, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['doctor'] }),
  })
  if (f.approval_status === 'approved') {
    return (
      <p className="mt-2 text-sm text-sev-normal">
        Approved by {f.approval_by}{f.approval_at && ` · ${fmtDateTime(f.approval_at)}`}
        {f.approval_note && <span className="text-ink">: {f.approval_note}</span>}
      </p>
    )
  }
  if (f.approval_status !== 'awaiting') return null
  return (
    <form className="mt-3 grid gap-2 rounded-md border border-sev-critical/50 bg-sev-critical/10 p-3 sm:grid-cols-[1fr_auto]"
      onSubmit={(e) => { e.preventDefault(); approve.mutate() }}>
      <p className="text-sm font-medium text-ink sm:col-span-2">
        Awaiting your approval. The astronaut was told the case went to the doctor and sees only the approved DEMO protocol.
      </p>
      <input className="h-9 rounded-md border border-seam-strong bg-deck px-2 text-sm text-ink outline-none focus:border-signal"
        placeholder="Your decision or advice (sent to the astronaut, audited)" value={note} maxLength={2000}
        onChange={(e) => setNote(e.target.value)} />
      <button className="h-9 rounded-md bg-ink px-4 text-sm font-semibold text-void disabled:opacity-50"
        disabled={!note.trim() || approve.isPending}>Approve</button>
      {approve.error && <div className="sm:col-span-2"><ErrorNote error={approve.error} /></div>}
    </form>
  )
}

export function FeedCard({ f, showName = true }: { f: FeedItem; showName?: boolean }) {
  const qc = useQueryClient()
  const seen = useMutation({
    mutationFn: () => doctorApi.seen(f.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['doctor'] }),
  })
  return (
    <li
      className={`rounded-md border bg-hull px-4 py-3 ${f.seen_at ? 'border-seam opacity-75' : 'border-seam-strong'}`}
      style={{ boxShadow: `inset 3px 0 0 ${LEVEL_COLOR[f.level]}` }}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <LevelDot level={f.level} title={`Rule: ${f.rule_level}; AI: ${f.ai_level ?? f.ai_status}`} />
        {showName && <span className="font-medium text-ink">{f.astronaut_name} <span className="num text-ink-faint">{f.astronaut_code}</span></span>}
        <span className="text-ink-dim">{SOURCE_LABEL[f.source_type]}</span>
        {f.is_simulated && <SimulatedMark />}
        <span className="num ml-auto text-xs text-ink-faint">{fmtDateTime(f.created_at)}</span>
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-ink">{f.text}</p>
      {f.rule_reasons.length > 0 && (
        <p className="mt-2 text-xs text-ink-dim">
          <span className="text-ink-faint">Rule flag ({f.rule_ids.join(', ')}): </span>
          {f.rule_reasons.join(' ')}
        </p>
      )}
      <AiBox f={f} />
      <Approval f={f} />
      <div className="mt-2 flex items-center justify-end gap-3 text-xs">
        {f.seen_at ? (
          <span className="text-ink-faint">Seen by {f.seen_by} · {fmtDateTime(f.seen_at)}</span>
        ) : (
          <button className="rounded border border-seam-strong px-2.5 py-1 text-ink-dim hover:border-signal hover:text-ink"
            disabled={seen.isPending} onClick={() => seen.mutate()}>Mark as seen</button>
        )}
      </div>
    </li>
  )
}

/** Live feed: polls every 3 s. */
export function LiveFeed({ astronautId, minLevel }: { astronautId?: number; minLevel?: FlagLevel }) {
  const feed = useQuery({
    queryKey: ['doctor', 'feed', astronautId ?? 'all', minLevel ?? 'any'],
    queryFn: () => doctorApi.feed({ astronaut_id: astronautId, min_level: minLevel, limit: 100 }),
    refetchInterval: 3_000,
  })
  if (feed.error) return <ErrorNote error={feed.error} />
  if (feed.isLoading) return <p className="text-sm text-ink-dim">Loading…</p>
  if (!feed.data?.length) return <Empty title="Nothing written yet">Messages, symptom reports and check-in notes appear here as soon as they are saved.</Empty>
  return <ul className="grid gap-2" aria-live="polite">{feed.data.map((f) => <FeedCard key={f.id} f={f} showName={!astronautId} />)}</ul>
}
