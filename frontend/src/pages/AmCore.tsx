// AM Core: the station intelligence's sealed log. Crew: no access. Doctor: AM's visible decisions
// always; the daily reports, behaviour and crew attitude, and the mission log only in an emergency
// (break the seal with a reason, two hours, the crew is told) or after mission return.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { useState } from 'react'
import { request } from '../api/client'
import { ErrorNote, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime } from '../lib/format'

interface CoreStatus {
  open: boolean
  open_reason: 'emergency' | 'mission_return' | null
  open_until: string | null
  mission_returned_at: string | null
  reports: number
  last_close_date: string | null
  unseals: { id: number; kind: string; actor: string; reason: string; opened_at: string; expires_at: string | null }[]
}
interface Decision {
  id: number; close_date: string; astronaut_code: string | null; category: string; level: string
  decision: string; rationale: string; rule_id: string; visible_to_doctor: boolean; executed: boolean
}
interface Report {
  id: number; kind: 'astronaut_day' | 'crew_day'; astronaut_code: string | null; close_date: string; mission_day: number | null
  facts: Record<string, any>; assessment: Record<string, any>; narrative: string; narrative_status: string
  narrative_model: string | null; checks: { unverified_numbers?: number[]; wording?: string[] }; is_simulated: boolean
}

const core = {
  status: () => request<CoreStatus>('GET', '/am/core/status'),
  unseal: (reason: string) => request<CoreStatus>('POST', '/am/core/unseal', { reason }),
  decisions: () => request<Decision[]>('GET', '/am/core/decisions'),
  days: () => request<string[]>('GET', '/am/core/days'),
  reports: (day: string) => request<Report[]>('GET', '/am/core/reports', undefined, { close_date: day }),
  log: () => request<Report[]>('GET', '/am/core/mission-log'),
  summary: () => request<Record<string, any>>('GET', '/am/core/summary'),
}

const LEVEL_COLOR: Record<string, string> = {
  priority: 'var(--color-sev-critical)', watch: 'var(--color-sev-watch)', routine: 'var(--color-ink-faint)',
}

function Eye({ open }: { open: boolean }) {
  // AM's eye: a dim ring while sealed, a bright pulsing core while open
  return (
    <svg viewBox="0 0 120 120" className="size-28 shrink-0" aria-hidden>
      <circle cx="60" cy="60" r="54" fill="none" stroke="var(--color-seam-strong)" strokeWidth="2" />
      <circle cx="60" cy="60" r="40" fill="none" stroke={open ? 'var(--color-sev-critical)' : 'var(--color-signal-dim)'} strokeWidth="1.5" strokeDasharray="4 6" />
      <motion.circle cx="60" cy="60" r="16" fill={open ? 'var(--color-sev-critical)' : 'var(--color-signal-dim)'}
        animate={{ opacity: open ? [0.55, 1, 0.55] : [0.35, 0.55, 0.35], r: open ? [15, 18, 15] : [14, 15, 14] }}
        transition={{ duration: open ? 1.6 : 4, repeat: Infinity, ease: 'easeInOut' }} />
    </svg>
  )
}

function Decisions() {
  const q = useQuery({ queryKey: ['core', 'decisions'], queryFn: core.decisions, refetchInterval: 15_000 })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data?.length) return <p className="text-sm text-ink-dim">No decisions yet. AM decides at each End Day.</p>
  return (
    <ul className="grid gap-1.5 text-sm">
      {q.data.slice(0, 60).map((d) => (
        <li key={d.id} className="rounded bg-deck px-3 py-2" style={{ boxShadow: `inset 3px 0 0 ${LEVEL_COLOR[d.level] ?? LEVEL_COLOR.routine}` }}>
          <span className="num text-xs text-ink-faint">{d.close_date} · {d.astronaut_code ?? 'crew'} · {d.category} · {d.rule_id} · {d.executed ? 'applied by AM' : 'recommendation'}{!d.visible_to_doctor && ' · sealed'}</span>
          <p className="text-ink">{d.decision}</p>
          <p className="text-xs text-ink-dim">Why: {d.rationale}</p>
        </li>
      ))}
    </ul>
  )
}

function ReportCard({ r }: { r: Report }) {
  const crew = r.kind === 'crew_day'
  const a = r.assessment
  return (
    <article className="rounded-lg border border-seam bg-hull p-4" style={{ boxShadow: crew ? 'inset 3px 0 0 var(--color-signal)' : `inset 3px 0 0 ${LEVEL_COLOR[a.attention] ?? LEVEL_COLOR.routine}` }}>
      <header className="flex flex-wrap items-baseline gap-2">
        <h3 className="font-semibold text-ink">{crew ? 'Crew report' : `${r.facts.astronaut?.name} · ${r.astronaut_code}`}</h3>
        <span className="num text-xs text-ink-faint">{r.close_date}{r.mission_day ? ` · mission day ${r.mission_day}` : ''}</span>
        {!crew && <span className="text-xs font-medium first-letter:uppercase" style={{ color: LEVEL_COLOR[a.attention] }}>{a.attention}</span>}
        {r.is_simulated && <SimulatedMark />}
        <span className="ml-auto text-xs text-ink-faint">
          {r.narrative_status === 'llm' ? `written by AM (${r.narrative_model})` : r.narrative_status === 'template' ? 'template (model offline)' : 'AM is writing…'}
        </span>
      </header>
      <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{r.narrative}</div>
      {!crew && a.behaviour?.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-ink-dim">{a.behaviour.map((b: string) => <li key={b}>{b}</li>)}</ul>
      )}
      {(r.checks.unverified_numbers?.length || r.checks.wording?.length) ? (
        <p className="mt-2 text-xs text-sev-watch">
          {r.checks.unverified_numbers?.length ? `Numbers not in the facts: ${r.checks.unverified_numbers.join(', ')}. ` : ''}
          {r.checks.wording?.length ? `Wording to check: ${r.checks.wording.join(', ')}.` : ''}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-ink-faint">{a.label}</p>
    </article>
  )
}

function DailyReports() {
  const days = useQuery({ queryKey: ['core', 'days'], queryFn: core.days })
  const [day, setDay] = useState<string | null>(null)
  const current = day ?? days.data?.[0] ?? null
  const reports = useQuery({ queryKey: ['core', 'reports', current], queryFn: () => core.reports(current!), enabled: !!current, refetchInterval: 10_000 })
  if (days.error) return <ErrorNote error={days.error} />
  if (!days.data?.length) return <p className="text-sm text-ink-dim">No sealed reports yet.</p>
  const list = reports.data ?? []
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-1">
        {days.data.map((d) => (
          <button key={d} onClick={() => setDay(d)} aria-pressed={d === current}
            className={`num rounded px-2 py-1 text-xs ${d === current ? 'bg-deck text-ink' : 'text-ink-dim hover:text-ink'}`}>{d}</button>
        ))}
      </div>
      {list.filter((r) => r.kind === 'crew_day').map((r) => <ReportCard key={r.id} r={r} />)}
      <div className="grid gap-3 xl:grid-cols-2">{list.filter((r) => r.kind === 'astronaut_day').map((r) => <ReportCard key={r.id} r={r} />)}</div>
    </div>
  )
}

function MissionLog() {
  const q = useQuery({ queryKey: ['core', 'log'], queryFn: core.log })
  const s = useQuery({ queryKey: ['core', 'summary'], queryFn: core.summary })
  if (q.error) return <ErrorNote error={q.error} />
  return (
    <div className="grid gap-3">
      {s.data && (
        <p className="text-sm text-ink-dim">
          {s.data.days_logged} day(s) logged · {s.data.decisions} decision(s) · flags by rule level{' '}
          {Object.entries(s.data.flags_by_rule_level ?? {}).map(([k, v]) => `${k} ${v}`).join(', ')}. <span className="text-ink-faint">{s.data.label}</span>
        </p>
      )}
      <ol className="grid gap-3">{q.data?.map((r, i) => (
        <li key={r.id}><p className="mb-1 text-xs text-ink-faint">Chapter {i + 1}</p><ReportCard r={r} /></li>
      ))}</ol>
    </div>
  )
}

export function AmCorePage() {
  const qc = useQueryClient()
  const st = useQuery({ queryKey: ['core', 'status'], queryFn: core.status, refetchInterval: 15_000 })
  const [reason, setReason] = useState('')
  const [tab, setTab] = useState<'reports' | 'log' | 'decisions'>('reports')
  const unseal = useMutation({ mutationFn: () => core.unseal(reason), onSuccess: () => qc.invalidateQueries({ queryKey: ['core'] }) })
  if (st.error) return <ErrorNote error={st.error} />
  if (!st.data) return <p className="text-ink-dim">Connecting to AM Core…</p>
  const s = st.data
  return (
    <div className="grid gap-8">
      <section className="flex flex-wrap items-center gap-6 rounded-lg border border-seam bg-hull p-5">
        <Eye open={s.open} />
        <div className="grid min-w-0 flex-1 gap-1">
          <h1 className="text-2xl">AM Core</h1>
          <p className="text-sm text-ink-dim">
            The station intelligence&apos;s own log: daily health, behaviour and crew-attitude reports, and the story of
            the whole mission. The crew cannot open it. Safety information is never held here: alerts and red flags
            reach you through the live feed as always.
          </p>
          <p className="text-sm">
            {s.open ? (
              <span className="text-sev-critical">
                OPEN · {s.open_reason === 'mission_return' ? 'mission returned' : `emergency, until ${s.open_until ? fmtDateTime(s.open_until) : '—'}`}
              </span>
            ) : <span className="text-signal">SEALED</span>}
            <span className="text-ink-faint"> · {s.reports} report(s){s.last_close_date ? `, last ${s.last_close_date}` : ''}</span>
          </p>
        </div>
        {!s.open && (
          <form className="grid w-full gap-2 sm:w-96" onSubmit={(e) => { e.preventDefault(); unseal.mutate() }}>
            <textarea className="min-h-16 rounded-md border border-sev-critical/50 bg-deck px-3 py-2 text-sm outline-none focus:border-sev-critical"
              placeholder="Emergency reason (logged, shown to the crew)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="h-9 rounded-md bg-sev-critical/80 px-4 text-sm font-semibold text-ink disabled:opacity-50" disabled={reason.trim().length < 10 || unseal.isPending}>
              Break the seal for 2 hours
            </button>
            {unseal.error && <ErrorNote error={unseal.error} />}
          </form>
        )}
      </section>

      {!s.open ? (
        <Section title="AM's decisions you can see" aside="attention, doctor notices, mission focus">
          <Decisions />
        </Section>
      ) : (
        <>
          <nav aria-label="AM Core" className="flex gap-1 border-b border-seam">
            {([['reports', 'Daily reports'], ['log', 'Mission log'], ['decisions', 'All decisions']] as const).map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} aria-current={tab === k ? 'page' : undefined}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}>{l}</button>
            ))}
          </nav>
          {tab === 'reports' && <DailyReports />}
          {tab === 'log' && <MissionLog />}
          {tab === 'decisions' && <Decisions />}
        </>
      )}

      {s.unseals.length > 0 && (
        <Section title="Seal history" aside="every opening is logged and told to the crew">
          <ul className="grid gap-1 text-xs text-ink-dim">
            {s.unseals.map((u) => <li key={u.id}>{fmtDateTime(u.opened_at)} · {u.kind} · {u.actor}: {u.reason}</li>)}
          </ul>
        </Section>
      )}
    </div>
  )
}
