// Today's duties: AM shares the station's work out by each astronaut's energy and morale.
// The astronaut sees their own duties and AM's note to them; the doctor sees the whole roster.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BAND_LABEL, dutiesApi, KIND_LABEL, type DutyAssignment, type DutyMember } from '../api/duties'
import { ErrorNote, Section } from './ui'

function Meter({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="grid gap-1 text-xs">
      <div className="flex justify-between text-ink-dim">
        <span>{label}</span>
        <span className="num text-ink">{value == null ? 'no check-in' : value}</span>
      </div>
      <div className="h-1.5 rounded-full bg-deck" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={value ?? undefined}>
        {value != null && <div className="h-full rounded-full bg-signal" style={{ width: `${Math.max(4, value)}%` }} />}
      </div>
    </div>
  )
}

function Duty({ t, canTick }: { t: DutyAssignment; canTick: boolean }) {
  const qc = useQueryClient()
  const toggle = useMutation({ mutationFn: () => dutiesApi.toggle(t.id), onSuccess: () => qc.invalidateQueries({ queryKey: ['duties'] }) })
  const done = t.status === 'done'
  return (
    <li className="flex gap-3 py-2.5">
      {canTick ? (
        <input type="checkbox" checked={done} onChange={() => toggle.mutate()} disabled={toggle.isPending}
          aria-label={`Mark ${t.title} done`} className="mt-1 size-4 shrink-0 accent-[var(--color-signal)]" />
      ) : <span aria-hidden className={`mt-2 size-1.5 shrink-0 rounded-full ${done ? 'bg-sev-normal' : 'bg-ink-faint'}`} />}
      <div className="min-w-0">
        <p className={`text-sm ${done ? 'text-ink-faint line-through' : 'text-ink'}`}>
          <span className="font-medium">{t.title}</span>
          <span className="ml-2 text-xs text-ink-faint">{KIND_LABEL[t.kind]}, {t.minutes} min</span>
        </p>
        <p className="text-xs text-ink-dim">{t.reason}</p>
      </div>
    </li>
  )
}

/** The astronaut's own duties, with AM's note written from their side. */
export function TodayDuties() {
  const q = useQuery({ queryKey: ['duties', 'today'], queryFn: dutiesApi.today, refetchInterval: 30_000 })
  const me = q.data?.crew[0]
  if (q.error) return <ErrorNote error={q.error} />
  if (!me) return null
  const done = me.assignments.filter((t) => t.status === 'done').length
  return (
    <section aria-label="Today's duties" className="grid gap-3 rounded-md border border-seam bg-hull p-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold">Today&apos;s duties</h2>
        <span className="text-sm text-ink-dim">{BAND_LABEL[me.band]}</span>
        <span className="num ml-auto text-sm text-ink-dim">{done} of {me.assignments.length} done</span>
      </header>
      {me.note && <p className="max-w-[70ch] border-l-2 border-signal/60 pl-3 text-ink">{me.note}</p>}
      <ul className="divide-y divide-seam">{me.assignments.map((t) => <Duty key={t.id} t={t} canTick />)}</ul>
    </section>
  )
}

function MemberRow({ m }: { m: DutyMember }) {
  return (
    <li className="grid gap-3 rounded-md border border-seam bg-hull p-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <div className="grid content-start gap-2.5">
        <div>
          <p className="font-semibold">{m.name}</p>
          <p className="text-xs text-ink-faint">{m.code}, {BAND_LABEL[m.band].toLowerCase()}</p>
        </div>
        <Meter label="Energy" value={m.energy} />
        <Meter label="Morale" value={m.morale} />
        <ul className="grid gap-1 text-xs text-ink-dim">{m.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
      <ul className="min-w-0 divide-y divide-seam">{m.assignments.map((t) => <Duty key={t.id} t={t} canTick={false} />)}</ul>
    </li>
  )
}

/** The doctor's view of the whole roster. */
export function DutyRosterPanel() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['duties', 'today', 'crew'], queryFn: dutiesApi.today, refetchInterval: 30_000 })
  const replan = useMutation({ mutationFn: dutiesApi.replan, onSuccess: (d) => qc.setQueryData(['duties', 'today', 'crew'], d) })
  return (
    <Section title="Today's duty roster"
      aside={<button onClick={() => replan.mutate()} disabled={replan.isPending}
        className="h-8 rounded border border-seam-strong px-3 text-sm text-ink hover:border-signal disabled:opacity-50">
        {replan.isPending ? 'Planning…' : 'Plan again from the latest check-ins'}</button>}>
      <p className="mb-3 max-w-[76ch] text-sm text-ink-dim">
        AM shares out the station&apos;s work by each person&apos;s energy and morale from check-ins, recorded sleep,
        active symptoms and open alerts. Private diaries are never read for this. {q.data?.label}.
      </p>
      {q.error && <ErrorNote error={q.error} />}
      {replan.error && <ErrorNote error={replan.error} />}
      {!!q.data?.postponed.length && (
        <p className="mb-3 rounded-md border border-sev-watch/40 bg-sev-watch/10 px-3 py-2 text-sm text-ink">
          Postponed: {q.data.postponed.map((t) => t.title).join(', ')}. Everyone who could take them is on a light day,
          so AM did not push them on anyone.
        </p>
      )}
      <ul className="grid gap-3">{q.data?.crew.map((m) => <MemberRow key={m.astronaut_id} m={m} />)}</ul>
    </Section>
  )
}
