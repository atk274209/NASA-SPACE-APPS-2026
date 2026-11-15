// Module M: routine examinations (DEMO intervals) and adherence points.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { request } from '../api/client'
import { ErrorNote } from './ui'

interface Exam {
  id: number
  exam_code: string
  title: string
  detail: string
  procedure_ref: string
  due_day: number
  state: 'upcoming' | 'due' | 'overdue' | 'done'
  done_day: number | null
  result_text: string
  entered_by: string | null
}
interface ExamsOut { mission_day: number | null; label: string; exams: Exam[] }
interface Points { points: number; level: number; next_level_at: number; rule: string }

const examsApi = {
  list: (id: number) => request<ExamsOut>('GET', '/exams', undefined, { astronaut_id: id }),
  result: (examId: number, text: string) => request<ExamsOut>('POST', `/exams/${examId}/result`, { result_text: text }),
  points: (id: number) => request<Points>('GET', '/points', undefined, { astronaut_id: id }),
}

const STATE_COLOR: Record<Exam['state'], string> = {
  overdue: 'var(--color-sev-warning)', due: 'var(--color-signal)', upcoming: 'var(--color-ink-faint)', done: 'var(--color-sev-normal)',
}

function ResultForm({ e, astronautId }: { e: Exam; astronautId: number }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const m = useMutation({
    mutationFn: () => examsApi.result(e.id, text),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['exams', astronautId] })
      qc.invalidateQueries({ queryKey: ['points', astronautId] })
    },
  })
  return (
    <form className="mt-2 flex flex-wrap gap-2" onSubmit={(ev) => { ev.preventDefault(); m.mutate() }}>
      <input className="h-8 min-w-0 flex-1 rounded border border-seam-strong bg-deck px-2 text-sm outline-none focus:border-signal"
        placeholder="Result as recorded by the procedure" value={text} maxLength={4000} onChange={(ev) => setText(ev.target.value)} />
      <button className="h-8 rounded bg-ink px-3 text-sm font-semibold text-void disabled:opacity-50" disabled={!text.trim() || m.isPending}>Save result</button>
      {m.error && <div className="w-full"><ErrorNote error={m.error} /></div>}
    </form>
  )
}

/** Exams list. `compact` shows only what is due or overdue (astronaut's Companion). */
export function RoutineExams({ astronautId, compact = false, canEnter = true }: { astronautId: number; compact?: boolean; canEnter?: boolean }) {
  const q = useQuery({ queryKey: ['exams', astronautId], queryFn: () => examsApi.list(astronautId), refetchInterval: 30_000 })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return null
  const list = compact ? q.data.exams.filter((e) => e.state === 'due' || e.state === 'overdue') : q.data.exams
  if (compact && !list.length) return null
  return (
    <section className="grid gap-2">
      <h2 className="text-sm font-semibold text-ink">Routine exams <span className="font-normal text-ink-faint">· {q.data.label}</span></h2>
      <ul className="grid gap-2">
        {list.map((e) => (
          <li key={e.id} className="rounded-md border border-seam bg-hull px-3 py-2 text-sm" style={{ boxShadow: `inset 3px 0 0 ${STATE_COLOR[e.state]}` }}>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium text-ink">{e.title}</span>
              <span className="text-xs font-medium first-letter:uppercase" style={{ color: STATE_COLOR[e.state] }}>{e.state}</span>
              <span className="num ml-auto text-xs text-ink-faint">due day {e.due_day}{e.done_day ? ` · done day ${e.done_day}` : ''} · {e.procedure_ref}</span>
            </div>
            {e.state === 'done' ? (
              <p className="mt-1 text-ink-dim">Result entered by {e.entered_by}: {e.result_text}</p>
            ) : (
              <p className="mt-1 text-xs text-ink-dim">{e.detail}</p>
            )}
            {canEnter && (e.state === 'due' || e.state === 'overdue') && <ResultForm e={e} astronautId={astronautId} />}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function PointsBadge({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['points', astronautId], queryFn: () => examsApi.points(astronautId), refetchInterval: 30_000 })
  if (!q.data) return null
  return (
    <span className="num inline-flex items-center gap-1.5 rounded border border-seam-strong px-2 py-0.5 text-xs text-ink-dim" title={q.data.rule}>
      Level <span className="text-ink">{q.data.level}</span> · {q.data.points} pts
    </span>
  )
}
