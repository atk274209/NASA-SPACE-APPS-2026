import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { guidanceApi, invalidateGuidance, type Escalation, type GuidanceLevel, type LevelGuidance } from '../api/guidance'
import { contextLabel, fmtAgo, fmtDateTime, severityColor } from '../lib/format'
import { useSession } from '../state/session'
import { Button, ErrorNote, Field, inputCls, KeyValue, Loading, Section, SeverityTag } from './ui'

type Entry = Pick<LevelGuidance, 'plain_meaning' | 'who_to_involve' | 'guidance' | 'procedure_id'>
type HeadingTag = 'h3' | 'h4' | 'h5'

/** Small label inside a panel; deliberately not a heading, so panels nest anywhere. */
function Label({ children }: { children: ReactNode }) {
  return <p className="mb-1 text-xs font-medium text-ink-dim">{children}</p>
}

/**
 * "Is this serious?" in plain language. The left rule and the headline carry the level
 * colour; the words carry the meaning, so colour is never the only signal.
 */
export function GuidancePanel({
  level,
  entry,
  label,
  reasons,
  appliesNow = true,
  title = 'Is this serious?',
  heading: Heading = 'h3',
  children,
}: {
  level: GuidanceLevel
  entry: Entry
  label: string
  reasons?: string[]
  appliesNow?: boolean
  title?: string
  heading?: HeadingTag // pick the level that fits where the panel is placed
  children?: ReactNode
}) {
  const color = severityColor(level)
  return (
    <section aria-label={title} className="flex flex-col gap-3 border-l-2 pl-4" style={{ borderColor: color }}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <Heading className="text-sm font-medium text-ink-dim">{title}</Heading>
        <span className="text-xs text-sev-watch" title="Placeholder wording until the mission's medical team replaces it">
          {label}
        </span>
      </div>
      <p className="text-xl leading-tight font-semibold" style={{ color: appliesNow ? color : undefined }}>
        {entry.plain_meaning}
      </p>
      {!appliesNow && <p className="text-sm text-ink-dim">Resolved. Shown for reference only.</p>}
      <KeyValue
        items={[
          ['Level', <SeverityTag key="level" severity={level} />],
          ['Who to involve', entry.who_to_involve],
          ['What to do', entry.guidance],
          ['Procedure', <span key="procedure" className="num text-ink-dim">{entry.procedure_id}</span>],
        ]}
      />
      {reasons && reasons.length > 0 && (
        <div>
          <Label>Why this level</Label>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink">
            {reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {children}
    </section>
  )
}

function AcknowledgeEscalation({ event }: { event: Escalation }) {
  const { operator, setOperator } = useSession()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  const ack = useMutation({
    mutationFn: () => guidanceApi.acknowledgeEscalation(event.id, operator.trim(), note.trim()),
    onSuccess: () => {
      setOpen(false)
      setNote('')
      invalidateGuidance(qc)
    },
  })
  if (!open) {
    return (
      <Button variant="quiet" className="mt-2 self-start" onClick={() => setOpen(true)}>
        Acknowledge escalation
      </Button>
    )
  }
  return (
    <form
      className="mt-2 flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (operator.trim()) ack.mutate()
      }}
    >
      <div className="grid gap-2 sm:grid-cols-[160px_1fr]">
        <Field label="Your name">
          <input className={inputCls} value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="e.g. Crew medical officer" />
        </Field>
        <Field label="Note (optional)">
          <input className={inputCls} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="Who was contacted, or what was done" />
        </Field>
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={!operator.trim() || ack.isPending}>
          {ack.isPending ? 'Saving…' : 'Confirm acknowledgement'}
        </Button>
        <Button type="button" variant="quiet" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {ack.error && <ErrorNote error={ack.error} />}
    </form>
  )
}

function EscalationRow({ e, showAlert }: { e: Escalation; showAlert: boolean }) {
  return (
    <li className="flex flex-col border-b border-seam/60 py-2.5 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <SeverityTag severity={e.level} />
        <span className="text-xs text-ink-faint" title={fmtDateTime(e.created_at)}>Raised {fmtAgo(e.created_at)}</span>
      </div>
      <p className="mt-0.5 text-sm text-ink">
        {e.plain_meaning}. <span className="text-ink-dim">Involve: {e.who_to_involve.toLowerCase()}.</span>
      </p>
      {showAlert && e.alert && (
        <Link to={`/alerts?id=${e.alert.id}`} className="text-sm text-signal hover:underline">
          {e.alert.display_name}, {contextLabel(e.alert.context).toLowerCase()}, {e.alert.direction} deviation
        </Link>
      )}
      {e.data_time && (
        <p className="text-xs text-ink-faint">
          Reached {e.level.toLowerCase()} with the reading at <span className="num">{fmtDateTime(e.data_time)}</span>
        </p>
      )}
      {e.status === 'open' ? (
        <AcknowledgeEscalation event={e} />
      ) : (
        <p className="mt-1 text-xs text-ink-dim">
          {e.status === 'acknowledged' ? 'Acknowledged' : 'Closed'}
          {e.acknowledged_by && <> by <span className="text-ink">{e.acknowledged_by}</span></>}
          {e.acknowledged_at && <>, <span className="num">{fmtDateTime(e.acknowledged_at)}</span></>}
          {e.acknowledgement_note && <>: {e.acknowledgement_note}</>}
        </p>
      )}
    </li>
  )
}

export function EscalationList({ events, showAlert = true }: { events: Escalation[]; showAlert?: boolean }) {
  return (
    <ul aria-label="Escalation events" className="flex flex-col">
      {events.map((e) => <EscalationRow key={e.id} e={e} showAlert={showAlert} />)}
    </ul>
  )
}

/** Guidance for one alert, with the escalation events it raised (Alerts detail view). */
export function AlertGuidance({ alertId }: { alertId: number }) {
  const q = useQuery({ queryKey: ['guidance', 'alert', alertId], queryFn: () => guidanceApi.forAlert(alertId), refetchInterval: 15_000 })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading label="Loading guidance" />
  const g = q.data
  return (
    <GuidancePanel level={g.level} entry={g} label={g.label} reasons={g.reasons} appliesNow={g.applies_now}>
      {g.escalations.length > 0 && (
        <div>
          <Label>Escalation events for this alert</Label>
          <EscalationList events={g.escalations} showAlert={false} />
        </div>
      )}
    </GuidancePanel>
  )
}

/** Guidance for one self-reported symptom (body map details). */
export function SymptomGuidance({ reportId, heading }: { reportId: number; heading?: HeadingTag }) {
  const q = useQuery({ queryKey: ['guidance', 'symptom', reportId], queryFn: () => guidanceApi.forSymptom(reportId) })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading label="Loading guidance" />
  const g = q.data
  return (
    <GuidancePanel level={g.level} entry={g} label={g.label} reasons={g.reasons} appliesNow={g.applies_now}
      heading={heading} />
  )
}

/** "Is this serious?" for the astronaut right now: the most serious open alert or active symptom. */
export function CurrentGuidance({ astronautId }: { astronautId: number }) {
  const q = useQuery({
    queryKey: ['guidance', 'current', astronautId],
    queryFn: () => guidanceApi.current(astronautId),
    refetchInterval: 15_000,
  })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading label="Loading guidance" />
  const g = q.data
  if (!g.level || !g.guidance) {
    return (
      <section aria-label="Is this serious?" className="flex flex-col gap-2 border-l-2 border-seam-strong pl-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-sm font-medium text-ink-dim">Is this serious?</h3>
          <span className="text-xs text-sev-watch">{g.label}</span>
        </div>
        <p className="text-sm text-ink">{g.note}</p>
      </section>
    )
  }
  const shown = g.items.slice(0, 4)
  return (
    <GuidancePanel level={g.level} entry={g.guidance} label={g.label}>
      <div>
        <Label>Based on</Label>
        <ul className="flex flex-col gap-2 text-sm">
          {shown.map((i) => (
            <li key={`${i.type}-${i.id}`}>
              <div className="flex flex-wrap items-center gap-x-2">
                <SeverityTag severity={i.level} />
                {i.type === 'alert' ? (
                  <Link to={`/alerts?id=${i.id}`} className="text-ink hover:text-signal">{i.title}</Link>
                ) : (
                  <span className="text-ink">{i.title}</span>
                )}
              </div>
              <p className="text-xs text-ink-dim">{i.reason}</p>
            </li>
          ))}
        </ul>
        {g.items.length > shown.length && (
          <p className="mt-1 text-xs text-ink-faint">and {g.items.length - shown.length} more open items</p>
        )}
      </div>
      <p className="text-xs text-ink-faint">{g.note}</p>
    </GuidancePanel>
  )
}

/** Open escalation events for the astronaut, each with a named acknowledgement (Overview). */
export function OpenEscalations({ astronautId }: { astronautId: number }) {
  const q = useQuery({
    queryKey: ['escalations', astronautId, 'open'],
    queryFn: () => guidanceApi.escalations(astronautId, 'open'),
    refetchInterval: 15_000,
  })
  return (
    <Section title="Open escalations" aside={q.data ? <span className="num">{q.data.length}</span> : undefined}>
      {q.isError ? (
        <ErrorNote error={q.error} />
      ) : !q.data ? (
        <Loading />
      ) : q.data.length === 0 ? (
        <p className="text-sm text-ink-dim">
          No open escalations. AMC raises one when an alert reaches a level that involves the crew medical officer.
        </p>
      ) : (
        <>
          <EscalationList events={q.data} />
          <p className="mt-2 text-xs text-ink-faint">
            Acknowledging records your name and note. It does not resolve the alert.
          </p>
        </>
      )}
    </Section>
  )
}
