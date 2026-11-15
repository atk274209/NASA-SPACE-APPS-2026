// Module L: values the astronaut said in conversation (one tap to record) and AMC's notes about them.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { notesApi, type ValueProposal } from '../api/notes'
import { fmtDateTime } from '../lib/format'
import { Empty, ErrorNote } from './ui'

const METRIC_LABEL: Record<string, string> = {
  heart_rate: 'Heart rate', sleep_duration: 'Sleep duration', water_intake: 'Water intake', body_temperature: 'Body temperature',
  spo2: 'Oxygen saturation', systolic_bp: 'Systolic blood pressure', diastolic_bp: 'Diastolic blood pressure',
}

function ValueCard({ p }: { p: ValueProposal }) {
  const qc = useQueryClient()
  const [context, setContext] = useState(p.context ?? '')
  const done = () => {
    qc.invalidateQueries({ queryKey: ['values', p.astronaut_id] })
    qc.invalidateQueries({ queryKey: ['notes', p.astronaut_id] })
  }
  const confirm = useMutation({ mutationFn: () => notesApi.confirmValue(p.id, context || null), onSuccess: done })
  const dismiss = useMutation({ mutationFn: () => notesApi.dismissValue(p.id), onSuccess: done })
  return (
    <li className="grid gap-2 rounded-md border border-seam-strong bg-hull px-3 py-2 text-sm sm:grid-cols-[1fr_auto] sm:items-center">
      <div>
        <p className="text-ink">
          {METRIC_LABEL[p.metric_code] ?? p.metric_code}: <span className="num">{p.value} {p.unit}</span>
        </p>
        <p className="text-xs text-ink-dim">You said: “{p.quote}”</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {!p.context && (
          <select aria-label="Activity context" value={context} onChange={(e) => setContext(e.target.value)}
            className="h-8 rounded border border-seam-strong bg-deck px-2 text-sm">
            <option value="">Context?</option>
            {p.allowed_contexts.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <button className="h-8 rounded bg-ink px-3 font-semibold text-void disabled:opacity-50"
          disabled={!context || confirm.isPending} onClick={() => confirm.mutate()}>Record</button>
        <button className="h-8 rounded border border-seam-strong px-3 text-ink-dim hover:text-ink"
          disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>Not right</button>
      </div>
      {(confirm.error || dismiss.error) && <div className="sm:col-span-2"><ErrorNote error={confirm.error ?? dismiss.error} /></div>}
    </li>
  )
}

/** Values AMC heard in the conversation; nothing is recorded until the astronaut taps Record. */
export function ValueProposals({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['values', astronautId], queryFn: () => notesApi.values(astronautId), refetchInterval: 5_000 })
  if (!q.data?.length) return null
  return (
    <section className="grid gap-2">
      <h2 className="text-sm font-semibold text-ink">Values AMC heard you say</h2>
      <ul className="grid gap-2">{q.data.map((p) => <ValueCard key={p.id} p={p} />)}</ul>
    </section>
  )
}

/** AMC's notes about the astronaut: always with the exact words they came from. */
export function NotesList({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['notes', astronautId], queryFn: () => notesApi.notes(astronautId), refetchInterval: 10_000 })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data?.length) return <Empty title="No notes yet">AMC writes a note when you mention a symptom or a value in conversation.</Empty>
  return (
    <ul className="grid gap-1.5 text-sm">
      {q.data.map((n) => (
        <li key={n.id} className="rounded bg-deck px-3 py-2">
          <span className="text-xs text-ink-faint first-letter:uppercase">{n.category}</span>
          <span className="num ml-2 text-xs text-ink-faint">{fmtDateTime(n.noted_at)}</span>
          <p className="text-ink">{n.text}</p>
          {n.quote && <p className="text-xs text-ink-dim">From: “{n.quote}”</p>}
        </li>
      ))}
    </ul>
  )
}
