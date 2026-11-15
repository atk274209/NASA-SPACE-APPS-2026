import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { streaksApi, type Streak, type StreakDay, type StreakDayState, type StreakMilestone } from '../api/streaks'
import { Empty, ErrorNote, Loading, Section, SimulatedMark } from '../components/ui'
import { titleCase } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

/** Where and how each routine is recorded. */
const ROUTINE: Record<string, { to: string; todo: string; done: string }> = {
  checkin: { to: '/companion', todo: 'check in or report a symptom', done: 'Checked in today.' },
  morning_check: { to: '/record', todo: 'record a resting heart rate taken before 12:00 UTC', done: 'Morning reading recorded today.' },
  exercise_logged: { to: '/record', todo: "log today's exercise", done: 'Exercise logged today.' },
  hydration_logged: { to: '/record', todo: "log today's water intake", done: 'Water intake logged today.' },
  missions_complete: { to: '/missions', todo: "complete today's priority tasks", done: "Today's priority tasks are done." },
}

const STATE_LABEL: Record<StreakDayState, string> = {
  done: 'recorded',
  missed: 'not recorded',
  no_data: 'no data in AMC',
  pending: 'today, still open',
}

const days = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`

/** Streak colours stay off the severity palette: streaks are about routine, never about health. */
function cellCls(d: Pick<StreakDay, 'state' | 'simulated'>): string {
  switch (d.state) {
    case 'done':
      return d.simulated ? 'sim-hatch bg-signal/45' : 'bg-signal'
    case 'missed':
      return 'border border-seam-strong bg-deck'
    case 'no_data':
      return 'border border-dashed border-seam'
    case 'pending':
      return 'border border-signal'
  }
}

function cellLabel(code: string, d: StreakDay): string {
  const state = code === 'missions_complete' && d.state === 'no_data' ? 'no mission list that day' : STATE_LABEL[d.state]
  return `Mission day ${d.mission_day} (${d.day_date}): ${state}${d.simulated ? ', simulated record' : ''}`
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-3" fill="none" stroke="currentColor" strokeWidth={2.4}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}

function CalendarStrip({ s, windowDays }: { s: Streak; windowDays: number }) {
  const pad = Math.max(0, windowDays - s.calendar.length)
  const count = (state: StreakDayState) => s.calendar.filter((d) => d.state === state).length
  const summary =
    `${s.title}, last ${days(s.calendar.length)}: ${count('done')} recorded, ${count('missed')} not recorded, ` +
    `${count('no_data')} without data${count('pending') ? ', today still open' : ''}`
  return (
    <ol
      aria-label={summary}
      className="grid w-full max-w-[28rem] gap-[2px] sm:gap-[3px]"
      style={{ gridTemplateColumns: `repeat(${windowDays}, minmax(0, 1fr))` }}
    >
      {/* Early in a mission there are fewer days than the window: keep today at the right edge. */}
      {Array.from({ length: pad }, (_, i) => <li key={`pad-${i}`} aria-hidden className="aspect-square" />)}
      {s.calendar.map((d) => {
        const label = cellLabel(s.code, d)
        return (
          <li key={d.mission_day} title={label} className={`aspect-square rounded-[2px] ${cellCls(d)}`}>
            <span className="sr-only">{label}</span>
          </li>
        )
      })}
    </ol>
  )
}

function MilestoneChip({ m }: { m: StreakMilestone }) {
  const detail = m.reached ? `first reached on mission day ${m.reached_day}` : 'not reached yet'
  return (
    <li
      title={`${titleCase(m.label)}, ${detail}`}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-px text-xs ${
        m.reached ? 'border-signal/60 text-signal' : 'border-seam text-ink-faint'
      }`}
    >
      {m.reached && <CheckIcon />}
      {days(m.days)}
      <span className="sr-only">: {detail}</span>
    </li>
  )
}

/** Encouraging, never shaming: an open day is an invitation, a restart is just a restart. */
function TodayLine({ s, simulated }: { s: Streak; simulated: boolean }) {
  const r = ROUTINE[s.code]
  if (s.today_done) return <p className="text-sm text-signal">{r ? r.done : 'Recorded today.'}</p>
  const todo = r ? <Link to={r.to} className="text-signal hover:underline">{r.todo}</Link> : 'record it'
  return (
    <p className="text-sm text-ink">
      {s.at_risk ? (
        <>Today is still open: {todo} to make it {days(s.current + 1)} in a row.</>
      ) : s.total_done === 0 ? (
        <>Not started yet: {todo} to begin.</>
      ) : (
        <>Start a new run today: {todo}.</>
      )}
      {s.code === 'checkin' && s.total_done === 0 && simulated && (
        <span className="block text-xs text-ink-faint">
          The simulator writes measurements but no check-ins, so this streak starts with the first real one.
        </span>
      )}
    </p>
  )
}

function Stat({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div className="w-24">
      <dt className="text-xs text-ink-dim">{label}</dt>
      <dd className="text-ink">
        <span className="num text-2xl leading-tight">{value}</span>{' '}
        <span className="text-sm text-ink-dim">{value === 1 ? 'day' : 'days'}</span>
      </dd>
      {note && <dd className="text-xs text-ink-faint">{note}</dd>}
    </div>
  )
}

function StreakRow({ s, windowDays, simulated }: { s: Streak; windowDays: number; simulated: boolean }) {
  const next = s.next_milestone
  const [a, b] = s.longest_days
  return (
    <li className="grid gap-x-8 gap-y-3 border-b border-seam/60 py-4 last:border-0 md:grid-cols-[minmax(0,1fr)_13rem] xl:grid-cols-[minmax(0,1fr)_13rem_28rem]">
      <div className="min-w-0">
        <h3 className="font-medium text-ink">{s.title}</h3>
        <p className="text-xs text-ink-dim">{s.rule}</p>
        <div className="mt-1.5">
          <TodayLine s={s} simulated={simulated} />
        </div>
      </div>

      <dl className="flex gap-5">
        <Stat label="Current" value={s.current} note={`${days(s.total_done)} in total`} />
        <Stat label="Longest" value={s.longest}
          note={a === undefined ? undefined : a === b ? `Mission day ${a}` : `Mission days ${a} to ${b}`} />
      </dl>

      <div className="flex min-w-0 flex-col gap-2.5 md:col-span-2 xl:col-span-1">
        <CalendarStrip s={s} windowDays={windowDays} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <ul aria-label={`${s.title}: adherence milestones`} className="flex flex-wrap gap-1.5">
            {s.milestones.map((m) => <MilestoneChip key={m.days} m={m} />)}
          </ul>
          <span className="text-xs text-ink-faint">
            {next === null
              ? 'Every adherence milestone reached.'
              : `${next - s.current} more ${next - s.current === 1 ? 'day' : 'days'} to reach ${next} in a row.`}
          </span>
        </div>
      </div>
    </li>
  )
}

function Legend({ simulated }: { simulated: boolean }) {
  const items: [string, Pick<StreakDay, 'state' | 'simulated'>][] = [
    ['Recorded', { state: 'done', simulated: false }],
    ['Recorded by the simulator', { state: 'done', simulated: true }],
    ['Not recorded', { state: 'missed', simulated: false }],
    ['No data in AMC that day', { state: 'no_data', simulated: false }],
    ['Today, still open', { state: 'pending', simulated: false }],
  ]
  return (
    <ul aria-label="Calendar legend" className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-ink-dim">
      {items.filter(([, d]) => simulated || !d.simulated).map(([label, d]) => (
        <li key={label} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={`size-3 rounded-[2px] ${cellCls(d)}`} />
          {label}
        </li>
      ))}
    </ul>
  )
}

export function StreaksPage() {
  const { astronaut, astronautsLoading } = useSession()
  const q = useQuery({
    queryKey: ['streaks', astronaut?.id],
    queryFn: () => streaksApi.get(astronaut!.id),
    enabled: !!astronaut,
    refetchInterval: 60_000,
    // A 4xx answer (no mission yet, unknown crew member) will not change on retry.
    retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
  })

  if (astronautsLoading) return <Loading />
  if (!astronaut) return <NoCrew />
  if (q.isError) {
    if (q.error instanceof ApiError && q.error.status === 422) {
      return (
        <Empty title="Streaks begin with the mission">
          {titleCase(q.error.message)}. Missions are set up in{' '}
          <Link to="/system" className="text-signal underline underline-offset-2">System</Link>, which can also generate
          a labelled simulated one.
        </Empty>
      )
    }
    return <ErrorNote error={q.error} />
  }
  if (!q.data) return <Loading />

  const r = q.data
  const doneToday = r.streaks.filter((s) => s.today_done).length
  const first = r.streaks[0]?.calendar[0]?.mission_day ?? r.mission_day
  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end gap-6 border-b border-seam pb-5">
        <div className="mr-auto max-w-4xl">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">Streaks</h1>
            {r.simulated && <SimulatedMark />}
          </div>
          <p className="text-ink-dim">Mission day <span className="num text-ink">{r.mission_day}</span></p>
          <p className="mt-2 text-sm text-ink-dim">{r.rules}</p>
        </div>
        <div className="w-56">
          <div className="flex justify-between text-sm">
            <span className="text-ink-dim">Recorded today</span>
            <span className="num">{doneToday}/{r.streaks.length}</span>
          </div>
          <div className="mt-1.5 flex gap-1" aria-hidden>
            {r.streaks.map((s) => (
              <span key={s.code} className={`h-1.5 flex-1 rounded-full ${s.today_done ? 'bg-signal' : 'bg-deck'}`} />
            ))}
          </div>
        </div>
      </div>

      <Section
        title="Routines"
        aside={first === r.mission_day ? `Mission day ${first}` : `Mission days ${first} to ${r.mission_day}`}
      >
        <ul>
          {r.streaks.map((s) => <StreakRow key={s.code} s={s} windowDays={r.window_days} simulated={r.simulated} />)}
        </ul>
      </Section>

      <div className="flex flex-col gap-2">
        <Legend simulated={r.simulated} />
        <p className="text-xs text-ink-faint">
          {r.milestone_note} Days are mission days in UTC; the calendar shows up to the last {r.window_days}.
          {r.simulated && ' Hatched days were counted from records generated by the AMC simulator.'}
        </p>
      </div>
    </div>
  )
}
