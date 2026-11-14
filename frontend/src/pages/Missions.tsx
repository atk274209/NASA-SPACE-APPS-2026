import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api, type DailyTask } from '../api/client'
import { symptomsApi, type SymptomReport } from '../api/symptoms'
import { ErrorNote, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtTime, titleCase } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const PRIORITY = { 1: 'Priority', 2: 'Routine', 3: 'Optional' } as Record<number, string>

function LinkedSymptoms({ ids, symptoms }: { ids: number[]; symptoms: Map<number, SymptomReport> }) {
  return (
    <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-xs">
      <span className="text-ink-dim">{ids.length === 1 ? 'For symptom' : 'For symptoms'}</span>
      {ids.map((id, i) => {
        const r = symptoms.get(id)
        return (
          <span key={id}>
            <Link to="/companion" className="text-signal hover:underline">
              {r ? `${r.label}, ${r.severity}` : `report #${id}`}
            </Link>
            {r?.status === 'resolved' && <span className="text-ink-faint"> (resolved)</span>}
            {i < ids.length - 1 && <span className="text-ink-faint">;</span>}
          </span>
        )
      })}
    </p>
  )
}

function TaskRow({ t, symptoms }: { t: DailyTask; symptoms: Map<number, SymptomReport> }) {
  const qc = useQueryClient()
  const { astronaut } = useSession()
  const m = useMutation({
    mutationFn: (status: string) => api.setTask(t.id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['today', astronaut?.id] }),
  })
  const done = t.status === 'done'
  const verified = t.completed_by === 'data'
  const refs = t.reason_refs as { type: string; id?: number }[]
  const alertRef = refs.find((r) => r.type === 'alert')
  const symptomIds = [...new Set(refs.filter((r) => r.type === 'symptom_report' && r.id != null).map((r) => r.id!))]
  return (
    <motion.li layout transition={{ duration: 0.2 }} className="grid grid-cols-[auto_1fr] gap-x-4 border-b border-seam/60 py-3 last:border-0">
      <input
        type="checkbox"
        aria-label={`Mark "${t.title}" as done`}
        checked={done}
        disabled={verified || m.isPending}
        onChange={(e) => m.mutate(e.target.checked ? 'done' : 'pending')}
        className="mt-1 size-4 accent-[var(--color-signal)]"
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className={`font-medium ${done ? 'text-ink-dim line-through decoration-ink-faint' : 'text-ink'}`}>{t.title}</span>
          <span className={`text-xs ${t.priority === 1 && !done ? 'text-signal' : 'text-ink-faint'}`}>
            {PRIORITY[t.priority]}, {t.category}
          </span>
        </div>
        <p className="text-sm text-ink-dim">{t.detail}</p>
        {symptomIds.length > 0 && <LinkedSymptoms ids={symptomIds} symptoms={symptoms} />}
        <p className="mt-1 text-xs text-ink-faint">
          Why today: {t.reason}
          {alertRef && <> <Link to={`/alerts?id=${alertRef.id}&filter=all`} className="text-signal hover:underline">Open alert</Link></>}
        </p>
        {done && (
          <p className="mt-1 text-xs text-sev-normal">
            {verified ? 'Confirmed from recorded data' : 'Marked done'}{t.completed_at ? ` at ${fmtTime(t.completed_at)}` : ''}
          </p>
        )}
        <p className="mt-0.5 text-[11px] text-ink-faint">Protocol {t.protocol_ref}</p>
        {m.error && <ErrorNote error={m.error} />}
      </div>
    </motion.li>
  )
}

export function MissionsPage() {
  const { astronaut } = useSession()
  const today = useQuery({
    queryKey: ['today', astronaut?.id],
    queryFn: () => api.today(astronaut!.id),
    enabled: !!astronaut,
    refetchInterval: 30_000,
  })
  const reports = useQuery({
    queryKey: ['symptoms', astronaut?.id],
    queryFn: () => symptomsApi.reports(astronaut!.id),
    enabled: !!astronaut,
  })
  const symptoms = useMemo(() => new Map((reports.data ?? []).map((r) => [r.id, r])), [reports.data])
  if (!astronaut) return <NoCrew />
  if (today.isError) return <ErrorNote error={today.error} />
  if (!today.data) return <Loading />
  const d = today.data
  const pct = d.total ? Math.round((d.done / d.total) * 100) : 0
  const groups = new Map<string, DailyTask[]>()
  for (const t of d.tasks) {
    const g = t.status === 'done' ? 'Completed' : t.priority === 1 ? 'Priority today' : 'Routine'
    groups.set(g, [...(groups.get(g) ?? []), t])
  }
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <div className="flex flex-wrap items-end gap-6 border-b border-seam pb-5">
        <div className="mr-auto">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">Daily missions</h1>
            {astronaut.is_simulated && <SimulatedMark />}
          </div>
          <p className="text-ink-dim">Mission day <span className="num text-ink">{d.mission_day}</span></p>
        </div>
        <div className="w-56">
          <div className="flex justify-between text-sm"><span className="text-ink-dim">Completed</span><span className="num">{d.done}/{d.total}</span></div>
          <div className="mt-1 h-1.5 rounded-full bg-deck">
            <motion.div className="h-full rounded-full bg-signal" initial={false} animate={{ width: `${pct}%` }} />
          </div>
        </div>
      </div>
      {['Priority today', 'Routine', 'Completed'].filter((g) => groups.has(g)).map((g) => (
        <Section key={g} title={g} aside={`${groups.get(g)!.length}`}>
          <ul>{groups.get(g)!.map((t) => <TaskRow key={t.id} t={t} symptoms={symptoms} />)}</ul>
        </Section>
      ))}
      <p className="text-xs text-ink-faint">
        Tasks adapt to your recent data, check-ins and the symptoms you report; resolving a symptom removes its open
        tasks. AM does not prescribe medication or change exercise or nutrition targets. {titleCase(d.protocol_note)}.
      </p>
    </div>
  )
}
