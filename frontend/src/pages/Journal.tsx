import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import {
  journalApi,
  UNMARKED_GENERATOR_VERSIONS,
  type DayLevel,
  type DayMessage,
  type JournalDay,
  type JournalItem,
  type JournalRef,
  type JournalSymptom,
} from '../api/journal'
import { Button, Empty, ErrorNote, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime, fmtNum, parseUtc, severityColor, unitLabel } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const STATUS_COLOR: Record<string, string> = {
  'below reference': 'var(--color-sev-watch)',
  'above reference': 'var(--color-sev-watch)',
  'within reference': 'var(--color-ink-dim)',
}

const LEVEL_STYLE: Record<DayLevel, { label: string; color: string }> = {
  red: { label: 'Red', color: 'var(--color-sev-critical)' },
  yellow: { label: 'Yellow', color: 'var(--color-sev-watch)' },
  green: { label: 'Green', color: 'var(--color-sev-normal)' },
  none: { label: 'No data', color: 'var(--color-ink-faint)' },
}

const NARRATIVE_TITLES = ['Mission continuity', 'Health status', 'Psychological status', 'Notes for the medical team']

const FALLBACK_REASON: Record<string, string> = {
  service_unreachable: 'the local language model service is not running',
  model_not_installed: 'the chat model is not installed',
  model_error: 'the language model returned an error',
  malformed_reply: 'the model reply could not be used',
  not_requested: 'it was not requested',
}

const isMarked = (e: JournalDay) => !UNMARKED_GENERATOR_VERSIONS.includes(e.generator_version)
const needsAiSummary = (e: JournalDay) =>
  !e.narrative || e.narrative_generated_by !== 'llm' || !!e.narrative_grounding?.outdated
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
/** Mission days are UTC days, so journal times are shown in UTC too. */
const utcTime = (ts: string) => `${parseUtc(ts).toISOString().slice(11, 16)} UTC`

function upsertDay(list: JournalDay[] | undefined, entry: JournalDay): JournalDay[] | undefined {
  if (!list) return list
  return [...list.filter((e) => e.mission_day !== entry.mission_day), entry].sort((a, b) => b.mission_day - a.mission_day)
}

/** Colour dot plus a text label, so colour is never the only signal. 'No data' is a hollow ring. */
function LevelDot({ level, label, className = '' }: { level: DayLevel | null; label: string; className?: string }) {
  const color = level ? LEVEL_STYLE[level].color : 'var(--color-ink-faint)'
  const hollow = level === null || level === 'none'
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} style={{ color }}>
      <span aria-hidden className={`size-2 shrink-0 rounded-full ${hollow ? 'border border-current' : ''}`}
        style={hollow ? undefined : { background: color }} />
      {label}
    </span>
  )
}

function DayLevelMark({ e, className = '' }: { e: JournalDay; className?: string }) {
  return isMarked(e)
    ? <LevelDot level={e.day_level} label={LEVEL_STYLE[e.day_level].label} className={className} />
    : <LevelDot level={null} label="Not marked" className={className} />
}

function Legend() {
  const q = useQuery({ queryKey: ['day-level-rules'], queryFn: journalApi.dayLevelRules, staleTime: Infinity })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return null
  const r = q.data
  return (
    <Section title="Day markers" aside={<span className="text-xs text-sev-watch">{r.label}</span>}>
      <ul className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 xl:grid-cols-4">
        {r.levels.map((l) => (
          <li key={l.level}>
            <LevelDot level={l.level} label={l.label} className="font-medium" />
            <span className="mt-0.5 block text-ink-dim">{l.description}</span>
          </li>
        ))}
      </ul>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-ink-dim hover:text-ink">DEMO rules ({r.rules.length})</summary>
        <ol className="mt-2 space-y-1.5">
          {r.rules.map((rule) => (
            <li key={rule.id} className="flex gap-3">
              <LevelDot level={rule.level} label={LEVEL_STYLE[rule.level].label} className="w-16 shrink-0" />
              <span>
                {rule.text}
                {rule.detail && <span className="block text-xs text-ink-faint">{rule.detail}</span>}
              </span>
            </li>
          ))}
        </ol>
        <p className="mt-2 text-xs text-ink-faint">{r.note} Rules version {r.version}.</p>
      </details>
    </Section>
  )
}

function RefLinks({ refs }: { refs: JournalRef[] }) {
  const alerts = refs.filter((r) => r.type === 'alert' && r.id != null)
  if (!alerts.length) return null
  return (
    <>
      {alerts.map((r) => (
        <span key={r.id}>
          {' '}
          <Link to={`/alerts?id=${r.id}&filter=all`} className="num text-signal hover:underline">Alert #{r.id}</Link>
        </span>
      ))}
    </>
  )
}

function DayMarker({ e }: { e: JournalDay }) {
  const rules = useQuery({ queryKey: ['day-level-rules'], queryFn: journalApi.dayLevelRules, staleTime: Infinity })
  if (!isMarked(e)) {
    return (
      <Section title="Day marker">
        <p className="text-sm text-ink-dim">This entry was written before day marking. Update the journal to mark it.</p>
      </Section>
    )
  }
  const reasons = e.day_level_reasons
  const description = rules.data?.levels.find((l) => l.level === e.day_level)?.description
  return (
    <Section title="Why this day is marked" aside={<DayLevelMark e={e} />}>
      {reasons.length === 0 ? (
        <p className="text-sm text-ink-dim">{description ?? 'No DEMO rule applies to this day.'}</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {reasons.map((r, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: LEVEL_STYLE[r.level].color }} />
              <span>
                {r.text}
                <span className="block text-xs text-ink-faint">
                  {LEVEL_STYLE[r.level].label} rule: {r.rule_text}
                  <RefLinks refs={r.refs} />
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function parseNarrative(text: string): { title: string | null; body: string }[] {
  return text.split(/\n{2,}/).map((p) => {
    const title = NARRATIVE_TITLES.find((t) => p.startsWith(`${t}: `))
    return title ? { title, body: p.slice(title.length + 2) } : { title: null, body: p }
  })
}

function Provenance({ e }: { e: JournalDay }) {
  const g = e.narrative_grounding ?? {}
  const input = g.input
  const sources = input
    ? `the journal entry, ${plural(input.symptoms_reported + input.symptoms_ongoing, 'symptom report')} and ${
      input.chat_messages_included === input.chat_messages
        ? plural(input.chat_messages, 'chat message')
        : `${input.chat_messages_included} of ${plural(input.chat_messages, 'chat message')}`}`
    : "this day's records"
  return (
    <div className="mt-3 space-y-1 text-xs">
      <p className="text-ink-faint">
        {e.narrative_generated_by === 'llm'
          ? `Drafted by AM from this day's records only (${sources}). A model draft, not a medical assessment.`
          : `Rule-based template from this day's records (${sources}). No language model was used because ${
            FALLBACK_REASON[g.fallback_reason ?? ''] ?? 'the model was unavailable'}.`}
        {e.narrative_generated_at && ` Written ${fmtDateTime(e.narrative_generated_at)}.`}
      </p>
      {g.unverified_numbers?.length ? (
        <p className="text-sev-warning">
          Numbers not found in this day&apos;s records: {g.unverified_numbers.join(', ')}. Check them before relying on this summary.
        </p>
      ) : null}
      {g.wording_flags?.length ? (
        <p className="text-sev-watch">
          Wording to check: {g.wording_flags.map((w) => `“${w}”`).join(', ')}. The model was told not to diagnose,
          explain causes or advise treatment.
        </p>
      ) : null}
      {g.outdated && (
        <p className="text-sev-watch">This day&apos;s records changed after the summary was written. Write it again to include them.</p>
      )}
    </div>
  )
}

function Narrative({ e, busy }: { e: JournalDay; busy: boolean }) {
  const qc = useQueryClient()
  const write = useMutation({
    mutationFn: () => journalApi.writeNarrative(e.astronaut_id, e.mission_day),
    onSuccess: (entry) => {
      qc.setQueryData<JournalDay[]>(['journal', e.astronaut_id], (old) => upsertDay(old, entry))
      qc.invalidateQueries({ queryKey: ['journal-conversations', e.astronaut_id, e.mission_day] })
    },
  })
  return (
    <Section
      title="AI summary"
      aside={
        <span className="flex items-center gap-3">
          {/* labelled here as well: the model does not always say so in its text */}
          {e.is_simulated && e.narrative && <SimulatedMark title="Written from simulated records." />}
          <Button variant="quiet" onClick={() => write.mutate()} disabled={busy || write.isPending}>
            {write.isPending ? 'Writing…' : e.narrative ? 'Write AI summary again' : 'Write AI summary'}
          </Button>
        </span>
      }
    >
      {write.error && <ErrorNote error={write.error} />}
      {!e.narrative ? (
        <p className="text-sm text-ink-dim">
          No summary yet. AM drafts one from this day&apos;s records only: mission continuity, health status, psychological
          status and notes for the medical team. Without the language model it writes a rule-based template instead.
        </p>
      ) : (
        <>
          <dl className="grid gap-x-6 gap-y-3 text-sm md:grid-cols-[180px_minmax(0,1fr)]">
            {parseNarrative(e.narrative).map((p, i) => p.title ? (
              <div key={i} className="contents">
                <dt className="text-ink-dim">{p.title}</dt>
                <dd className="leading-relaxed">{p.body}</dd>
              </div>
            ) : <dd key={i} className="leading-relaxed md:col-span-2">{p.body}</dd>)}
          </dl>
          <Provenance e={e} />
        </>
      )}
    </Section>
  )
}

function SymptomList({ title, items, ongoing = false }: { title: string; items: JournalSymptom[]; ongoing?: boolean }) {
  return (
    <div>
      <h3 className="text-ink-dim">{title}</h3>
      <ul className="mt-1 space-y-2">
        {items.map((x) => (
          <li key={x.id}>
            <span className="font-medium text-ink">{x.label}</span>, {x.severity} (self-rated)
            {x.report_promptly && <> <span className="ml-1 text-xs text-sev-critical">DEMO report-promptly list</span></>}
            <span className="block text-xs text-ink-faint">
              {ongoing
                ? x.reported_day < 1 ? 'Reported before the mission start' : `Reported on mission day ${x.reported_day}`
                : `Reported at ${utcTime(x.reported_at)}`}
              , source {x.source.replace('_', ' ')}.{' '}
              {x.status_at_end_of_day === 'resolved'
                ? `Resolved${x.resolved_at ? ` at ${utcTime(x.resolved_at)}` : ''} by the end of the day.`
                : 'Still active at the end of the day.'}
            </span>
            {x.note && <span className="block text-ink-dim">“{x.note}”</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Symptoms({ e }: { e: JournalDay }) {
  const sym = e.sections.symptoms
  return (
    <Section title="Symptoms" aside={sym ? `${sym.reported.length} reported` : undefined}>
      {!sym ? (
        <p className="text-sm text-ink-dim">Update the journal to list this day&apos;s symptom reports.</p>
      ) : sym.reported.length === 0 && sym.ongoing.length === 0 ? (
        <p className="text-sm text-ink-dim">No symptoms were reported or ongoing on this day.</p>
      ) : (
        <div className="space-y-4 text-sm">
          {sym.reported.length > 0 && <SymptomList title="Reported this day" items={sym.reported} />}
          {sym.ongoing.length > 0 && <SymptomList title="Ongoing from earlier days" items={sym.ongoing} ongoing />}
        </div>
      )}
    </Section>
  )
}

const CLAMP = 280

function TimelineItem({ m }: { m: DayMessage }) {
  const [open, setOpen] = useState(false)
  const long = m.content.length > CLAMP
  const who = m.role === 'user' ? 'Astronaut' : m.generated_by === 'deterministic' ? 'AM, rule-based' : 'AM'
  const device = m.role === 'user' ? m.device_name ?? (m.client_host ? `host ${m.client_host}` : 'device not recorded') : null
  const title = m.conversation_title.length > 40 ? `${m.conversation_title.slice(0, 40)}…` : m.conversation_title
  return (
    <li className={`border-l-2 pl-3 ${m.role === 'user' ? 'border-signal/60' : 'border-seam-strong'}`}>
      <p className="text-xs text-ink-faint">
        <span className="num text-ink-dim">{utcTime(m.created_at)}</span>, {who}
        {device && `, ${device}`}, conversation <span className="num">#{m.conversation_id}</span> “{title}”
      </p>
      <p className="mt-0.5 whitespace-pre-wrap text-ink">{long && !open ? `${m.content.slice(0, CLAMP).trimEnd()}…` : m.content}</p>
      {long && (
        <button onClick={() => setOpen(!open)} aria-expanded={open} className="text-xs text-signal hover:underline">
          {open ? 'Show less' : 'Show full message'}
        </button>
      )}
    </li>
  )
}

function Conversations({ astronautId, day }: { astronautId: number; day: number }) {
  const q = useQuery({
    queryKey: ['journal-conversations', astronautId, day],
    queryFn: () => journalApi.conversations(astronautId, day),
  })
  const msgs = q.data ?? []
  const conversations = new Set(msgs.map((m) => m.conversation_id)).size
  return (
    <Section title="Conversations" aside={q.data ? `${plural(msgs.length, 'message')} in ${plural(conversations, 'conversation')}` : undefined}>
      {q.isError ? <ErrorNote error={q.error} /> : q.isLoading ? <Loading /> : msgs.length === 0 ? (
        <p className="text-sm text-ink-dim">No conversations on this day.</p>
      ) : (
        <ol className="max-h-[32rem] space-y-3 overflow-y-auto pr-1 text-sm">
          {msgs.map((m) => <TimelineItem key={m.id} m={m} />)}
        </ol>
      )}
      <p className="mt-3 text-xs text-ink-faint">
        All of this astronaut&apos;s conversations on this mission day. Times are UTC, the clock that defines mission days.
        A device name appears when the message came from a signed-in crew device.
      </p>
    </Section>
  )
}

function Rows({ items }: { items: JournalItem[] }) {
  if (!items.length) return <p className="text-sm text-ink-faint">No data recorded.</p>
  return (
    <table className="w-full text-sm">
      <tbody>
        {items.map((i) => (
          <tr key={i.key} className="border-b border-seam/60 last:border-0">
            <td className="py-1.5 pr-3">{i.label}</td>
            <td className="num py-1.5 pr-3 text-right">{fmtNum(i.value, 2)} <span className="text-ink-dim">{unitLabel(i.unit)}</span></td>
            <td className="py-1.5 text-right" style={{ color: STATUS_COLOR[i.status ?? ''] ?? 'var(--color-ink-faint)' }}>
              {i.status}
              {i.difference != null && i.status !== 'within reference' && (
                <span className="num ml-1 text-ink-dim">({i.difference > 0 ? '+' : ''}{fmtNum(i.difference, 2)}{i.percent != null ? `, ${i.percent > 0 ? '+' : ''}${i.percent}%` : ''})</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Day({ e, busy }: { e: JournalDay; busy: boolean }) {
  const s = e.sections
  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-wrap items-baseline gap-3 border-b border-seam pb-3">
        <h2 className="num text-2xl font-semibold">Mission day {String(e.mission_day).padStart(3, '0')}</h2>
        <span className="text-ink-dim">{e.day_date}</span>
        <DayLevelMark e={e} className="font-medium" />
        {!e.complete_day && <span className="text-sm text-sev-watch">Day in progress</span>}
        {e.is_simulated && <SimulatedMark />}
      </header>
      <DayMarker e={e} />
      <Narrative e={e} busy={busy} />
      <div className="grid gap-8 xl:grid-cols-2">
        <Symptoms e={e} />
        <Conversations astronautId={e.astronaut_id} day={e.mission_day} />
      </div>
      {e.flags.length > 0 && (
        <Section title="Flags">
          <ul className="space-y-1.5 text-sm">
            {e.flags.map((f, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: f.severity === 'review' ? 'var(--color-sev-watch)' : f.severity === 'info' ? 'var(--color-ink-faint)' : severityColor(f.severity) }} />
                {f.text}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Physical"><Rows items={s.physical} /></Section>
        <Section title="Mental (self-reported)"><Rows items={s.mental} /></Section>
      </div>
      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Observations">
          {e.observations.length ? <ul className="list-disc space-y-1 pl-5 text-sm">{e.observations.map((o, i) => <li key={i}>{o.text}</li>)}</ul>
            : <p className="text-sm text-ink-faint">No persistent deviations or trends.</p>}
        </Section>
        <Section title="Missions and alerts">
          <p className="text-sm">
            {s.tasks ? <>Tasks completed: <span className="num">{s.tasks.done}/{s.tasks.total} ({s.tasks.percent}%)</span></> : 'No tasks were planned for this day.'}
          </p>
          <p className="text-sm">New alerts: <span className="num">{s.alerts.new.length}</span>, open at end of day: <span className="num">{s.alerts.open_at_end_of_day}</span></p>
          {s.notes.length > 0 && <ul className="mt-2 text-sm text-ink-dim">{s.notes.map((n, i) => <li key={i}>“{n}”</li>)}</ul>}
        </Section>
      </div>
      <p className="text-xs text-ink-faint">
        Comparisons use this astronaut&apos;s own mission days 1 to 7 as a fixed reference period. They are descriptive
        and do not raise alerts. The day marker follows DEMO rules from records known by the end of the day. Generated by{' '}
        {e.generator_version} from stored records.
      </p>
    </article>
  )
}

/** Writes summaries one day at a time, so progress is visible and the run can be stopped between days. */
function useSummaryRun(astronautId: number | undefined) {
  const qc = useQueryClient()
  const [progress, setProgress] = useState<{ day: number; done: number; total: number } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const stopped = useRef(false)
  useEffect(() => () => { stopped.current = true }, [astronautId])

  const run = async (days: number[]) => {
    if (astronautId == null || !days.length) return
    stopped.current = false
    setError(null)
    for (let i = 0; i < days.length && !stopped.current; i++) {
      setProgress({ day: days[i], done: i, total: days.length })
      try {
        const entry = await journalApi.writeNarrative(astronautId, days[i])
        qc.setQueryData<JournalDay[]>(['journal', astronautId], (old) => upsertDay(old, entry))
      } catch (err) {
        setError(err)
        break
      }
    }
    setProgress(null)
  }
  return { progress, error, run, stop: () => { stopped.current = true } }
}

function AiLine() {
  const ai = useQuery({ queryKey: ['ai-status'], queryFn: api.aiStatus, refetchInterval: 20_000, retry: 0 })
  if (ai.isLoading) return <span className="text-sm text-ink-dim">Checking model…</span>
  if (ai.data?.reachable && ai.data.model_installed) {
    return <span className="text-sm text-ink-dim">Summaries by AM</span>
  }
  return (
    <span className="text-sm text-sev-watch">
      {ai.data?.reachable ? 'Local AI not set up' : 'Language model offline'}: summaries use the rule-based template
    </span>
  )
}

export function JournalPage() {
  const { astronaut } = useSession()
  const qc = useQueryClient()
  // the selected mission day is kept across astronauts, to compare crew members on the same day
  const [day, setDay] = useState<number | null>(null)
  const q = useQuery({ queryKey: ['journal', astronaut?.id], queryFn: () => journalApi.list(astronaut!.id), enabled: !!astronaut })
  const gen = useMutation({
    mutationFn: () => journalApi.generate(astronaut!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['journal', astronaut?.id] })
      qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
  const summaries = useSummaryRun(astronaut?.id)

  if (!astronaut) return <NoCrew />
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading />
  const entries = q.data
  const current = entries.find((e) => e.mission_day === day) ?? entries.find((e) => e.complete_day) ?? entries[0]
  const missing = entries.filter(needsAiSummary).map((e) => e.mission_day).sort((a, b) => a - b)
  const busy = summaries.progress !== null || gen.isPending
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="mr-auto text-2xl font-semibold">Mission journal</h1>
        {astronaut.is_simulated && <SimulatedMark />}
        <AiLine />
        {summaries.progress ? (
          <>
            <span role="status" className="text-sm text-ink-dim">
              Writing day {summaries.progress.day}, {summaries.progress.done + 1} of {summaries.progress.total}…
            </span>
            <Button variant="quiet" onClick={summaries.stop}>Stop</Button>
          </>
        ) : entries.length > 0 && (
          // days without a current model-written summary, oldest first
          <Button variant="quiet" onClick={() => summaries.run(missing)} disabled={!missing.length || busy}>
            {missing.length ? `Write missing AI summaries (${missing.length})` : 'All days have AI summaries'}
          </Button>
        )}
        <Button onClick={() => gen.mutate()} disabled={busy}>{gen.isPending ? 'Writing…' : entries.length ? 'Update journal' : 'Write journal'}</Button>
      </div>
      {gen.error && <ErrorNote error={gen.error} />}
      {summaries.error != null && <ErrorNote error={summaries.error} />}
      {!entries.length ? (
        <Empty title="No journal entries yet">Write the journal to summarise each mission day from stored records.</Empty>
      ) : (
        <>
          <Legend />
          <div className="grid gap-8 lg:grid-cols-[180px_minmax(0,1fr)]">
            <nav aria-label="Mission days" className="flex max-h-[70vh] flex-col overflow-y-auto pr-1">
              {entries.map((e) => (
                <button key={e.id} onClick={() => setDay(e.mission_day)} aria-current={e.id === current?.id}
                  className={`flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm ${e.id === current?.id ? 'bg-deck text-ink' : 'text-ink-dim hover:bg-deck/50'}`}>
                  <span className="num">Day {e.mission_day}</span>
                  <DayLevelMark e={e} className="text-xs" />
                </button>
              ))}
            </nav>
            {current && <Day e={current} busy={busy} />}
          </div>
        </>
      )}
    </div>
  )
}
