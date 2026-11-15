// Doctor landing page: the crew at a glance and the live feed of what they write.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { doctorApi, type CrewMember, type EscalationItem, type FlagLevel } from '../api/doctor'
import { CrewBento } from '../components/CrewBento'
import { FeedCard, LEVEL_COLOR, LevelDot, LiveFeed } from '../components/Feed'
import { guidanceApi } from '../api/guidance'
import { useAuth } from '../state/auth'
import { ErrorNote, Section, SimulatedMark } from '../components/ui'
// Mission time can run ahead of this computer's clock (End Day), so show times, not "ago".
import { fmtDateTime } from '../lib/format'
import { useSession } from '../state/session'
import { DutyRosterPanel } from '../components/Duties'

function CrewCard({ c }: { c: CrewMember }) {
  const nav = useNavigate()
  const { selectAstronaut } = useSession()
  return (
    <li>
      <button
        onClick={() => {
          selectAstronaut(c.astronaut_id)
          nav(`/patient/${c.astronaut_id}`)
        }}
        className="grid w-full gap-1 rounded-md border border-seam bg-hull px-4 py-3 text-left hover:border-signal"
        style={{ boxShadow: `inset 3px 0 0 ${LEVEL_COLOR[c.level]}` }}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold text-ink">{c.display_name}</span>
          <LevelDot level={c.level} />
        </div>
        <span className="text-sm text-ink-dim">
          <span className="num">{c.code}</span>{c.callsign && ` · ${c.callsign}`}{c.crew_role && ` · ${c.crew_role}`}
        </span>
        <span className="text-sm text-ink-dim">
          {c.mission_day ? <>Mission day <span className="num text-ink">{c.mission_day}</span></> : 'No mission'}
          {c.is_simulated && <span className="ml-2"><SimulatedMark /></span>}
        </span>
        <span className="num text-sm">
          <span style={{ color: LEVEL_COLOR.red }}>{c.unseen_red} red</span>
          <span className="text-ink-faint"> · </span>
          <span style={{ color: LEVEL_COLOR.yellow }}>{c.unseen_yellow} yellow</span>
          <span className="text-ink-faint"> unseen · {c.open_alerts} open alert{c.open_alerts === 1 ? '' : 's'}</span>
          {c.overdue_exams > 0 && <span style={{ color: 'var(--color-sev-warning)' }}> · {c.overdue_exams} exam{c.overdue_exams === 1 ? '' : 's'} overdue</span>}
        </span>
        {c.chronic_conditions.length > 0 && (
          <span className="text-xs text-ink-faint">Known conditions (fictional): {c.chronic_conditions.join(', ')}</span>
        )}
        {c.latest && (
          <span className="line-clamp-2 text-xs text-ink-dim">
            Latest ({fmtDateTime(c.latest.created_at)}): {c.latest.text}
          </span>
        )}
      </button>
    </li>
  )
}

function EscalationCard({ e }: { e: EscalationItem }) {
  const qc = useQueryClient()
  const { user } = useAuth()
  const [note, setNote] = useState('')
  const ack = useMutation({
    mutationFn: () => guidanceApi.acknowledgeEscalation(e.id, user.display_name, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['doctor'] }),
  })
  return (
    <li className="rounded-md border border-seam-strong bg-hull px-4 py-3" style={{ boxShadow: `inset 3px 0 0 ${LEVEL_COLOR.red}` }}>
      <p className="text-sm"><span className="font-medium">{e.astronaut_name}</span> <span className="num text-ink-faint">{e.astronaut_code}</span>
        <span className="text-ink-dim"> · alert escalation {e.level} · {e.procedure_id}</span></p>
      <p className="mt-1 text-ink">{e.plain_meaning}: {e.who_to_involve}. {e.guidance}</p>
      <p className="text-xs text-ink-faint">{e.label}</p>
      <form className="mt-2 flex flex-wrap gap-2" onSubmit={(ev) => { ev.preventDefault(); ack.mutate() }}>
        <input className="h-9 min-w-0 flex-1 rounded-md border border-seam-strong bg-deck px-2 text-sm outline-none focus:border-signal"
          placeholder="Note (audited)" value={note} onChange={(ev) => setNote(ev.target.value)} />
        <button className="h-9 rounded-md bg-ink px-4 text-sm font-semibold text-void disabled:opacity-50" disabled={ack.isPending}>Acknowledge</button>
      </form>
    </li>
  )
}

function ApprovalQueue() {
  const q = useQuery({ queryKey: ['doctor', 'approvals'], queryFn: doctorApi.approvals, refetchInterval: 3_000 })
  if (q.error) return <ErrorNote error={q.error} />
  const n = (q.data?.awaiting.length ?? 0) + (q.data?.open_escalations.length ?? 0)
  if (!n) return null
  return (
    <Section title={`Awaiting doctor approval (${n})`} aside="rule-based, never decided by the AI">
      <ul className="grid gap-2">
        {q.data?.awaiting.map((f) => <FeedCard key={`f${f.id}`} f={f} />)}
        {q.data?.open_escalations.map((e) => <EscalationCard key={`e${e.id}`} e={e} />)}
      </ul>
    </Section>
  )
}

export function CrewPage() {
  const crew = useQuery({ queryKey: ['doctor', 'crew'], queryFn: doctorApi.crew, refetchInterval: 5_000 })
  const [minLevel, setMinLevel] = useState<FlagLevel | undefined>(undefined)
  const filters: [FlagLevel | undefined, string][] = [[undefined, 'All'], ['yellow', 'Yellow and red'], ['red', 'Red only']]
  return (
    <div className="grid gap-8">
      <ApprovalQueue />
      <Section title="Crew analytics" aside="live · from stored records · SIMULATED test data">
        <CrewBento />
      </Section>
      <DutyRosterPanel />
      <Section title="Crew" aside="Earth-side medical console · updates every few seconds">
        {crew.error && <ErrorNote error={crew.error} />}
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {crew.data?.map((c) => <CrewCard key={c.astronaut_id} c={c} />)}
        </ul>
      </Section>
      <Section
        title="Live feed: what the crew wrote"
        aside={
          <span className="inline-flex gap-1">
            {filters.map(([lvl, label]) => (
              <button key={label} onClick={() => setMinLevel(lvl)} aria-pressed={minLevel === lvl}
                className={`rounded px-2 py-0.5 text-xs ${minLevel === lvl ? 'bg-deck text-ink' : 'text-ink-dim hover:text-ink'}`}>
                {label}
              </button>
            ))}
          </span>
        }
      >
        <p className="mb-3 text-xs text-ink-faint">
          Colour = the higher of the DEMO rule flag and the local model's suggestion. The model can raise a flag,
          never lower one, and it starts no escalation. Not clinically validated.
        </p>
        <LiveFeed minLevel={minLevel} />
      </Section>
    </div>
  )
}
