import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  crewApi,
  type EntryMetric,
  type EntrySpec,
  type EntryUnit,
  type ManualEntryResult,
  type ManualEntryRow,
} from '../api/crew'
import {
  Button,
  DemoThresholdNote,
  ErrorNote,
  inputCls,
  KeyValue,
  Loading,
  Section,
  SeverityTag,
  SimulatedMark,
} from '../components/ui'
import { fmtDateTime, fmtZ } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const RADIATION = 'radiation_dose'
const METRIC_KEY = 'am.record.metric'

// ------------------------------------------------------------------ helpers
function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}
function writeLocal(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: the form just starts from the first indicator */
  }
}

/** Accepts "36.8", "36,8", "-2" or ".5". Anything else is not a number. */
function parseDecimal(raw: string): number | null {
  const t = raw.trim().replace(',', '.')
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(t)) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Current local time in the format a datetime-local input expects (minute precision). */
function localNow(): string {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** How far `d` lies ahead of the present, in minutes (negative when it is in the past). */
const minutesAhead = (d: Date) => (d.getTime() - Date.now()) / 60_000

/**
 * Six significant digits are plenty on screen; the stored value keeps full precision.
 * Large numbers are grouped with thin spaces (SI style), which cannot be mistaken for a
 * decimal comma.
 */
function fmtValue(v: number): string {
  const s = String(Number(v.toPrecision(6)))
  if (Math.abs(v) < 10_000 || s.includes('e')) return s
  const [int, frac] = s.split('.')
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return frac ? `${grouped}.${frac}` : grouped
}

// Same linear rule as the backend: canonical = (entered + offset) × factor.
const toCanonical = (u: EntryUnit, v: number) => (v + u.offset) * u.factor
const fromCanonical = (u: EntryUnit, c: number) => c / u.factor - u.offset

function withinBounds(metric: EntryMetric, canonical: number): boolean {
  return (
    (metric.plausible_min === null || canonical >= metric.plausible_min) &&
    (metric.plausible_max === null || canonical <= metric.plausible_max)
  )
}

const BOUND_TEXT = 'The accepted range is an engineering check for entry errors, not a clinical range.'

// Inputs with a validation message get a red border while the message is shown.
const fieldCls = `${inputCls} aria-[invalid=true]:border-sev-critical`

const NOTE_TEXT: Record<string, string> = {
  unit_mismatch: 'This unit is not accepted for the indicator',
  below_sanity_bound: 'Below the accepted range',
  above_sanity_bound: 'Above the accepted range',
  non_finite_value: 'The value is not a number',
  context_not_allowed: 'This context is not allowed for the indicator',
  timestamp_in_future: 'The time is in the future',
  confidence_out_of_range: 'Confidence must be between 0 and 1',
  source_signal_quality: 'The source reported poor signal quality',
  low_confidence: 'Low confidence',
  unknown_metric: 'Unknown indicator',
}

/** Validation notes are "code: detail"; show the code as a sentence and keep the detail. */
function describeNote(note: string): string {
  const [code, ...rest] = note.split(':')
  const detail = rest.join(':').trim().replace(/\bdegC\b/g, '°C')
  const text = NOTE_TEXT[code]
  if (!text) return note
  return detail ? `${text}: ${detail}` : text
}

function contextName(spec: EntrySpec, code: string): string {
  return spec.contexts.find((c) => c.code === code)?.label ?? code
}

function rangeText(metric: EntryMetric, unit: EntryUnit): string | null {
  const { plausible_min: min, plausible_max: max } = metric
  if (min === null || max === null) return null
  const canonical = `${fmtValue(min)} to ${fmtValue(max)} ${metric.unit_label}`
  if (unit.canonical) return canonical
  return `${fmtValue(fromCanonical(unit, min))} to ${fmtValue(fromCanonical(unit, max))} ${unit.label} (${canonical})`
}

// ------------------------------------------------------------------ form
function FormRow({
  id,
  label,
  notes,
  children,
}: {
  id: string
  label: string
  notes?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 text-sm">
      <label htmlFor={id} className="text-ink-dim">{label}</label>
      {children}
      {notes && <div id={`${id}-notes`} className="flex flex-col gap-0.5 text-xs">{notes}</div>}
    </div>
  )
}

function EntryForm({
  spec,
  astronautId,
  onRecorded,
}: {
  spec: EntrySpec
  astronautId: number
  onRecorded: (r: ManualEntryResult) => void
}) {
  const qc = useQueryClient()
  const ids = useId()
  const byCode = useMemo(() => new Map(spec.metrics.map((m) => [m.code, m])), [spec])

  // Start from the indicator used last on this device: daily logging is often the same one.
  const [metricCode, setMetricCode] = useState(() => (byCode.get(readLocal(METRIC_KEY) ?? '') ?? spec.metrics[0]).code)
  const metric = byCode.get(metricCode) ?? spec.metrics[0]
  const [context, setContext] = useState(metric.contexts[0])
  const [unit, setUnit] = useState(metric.unit)
  const [valueRaw, setValueRaw] = useState('')
  const [timeLocal, setTimeLocal] = useState(localNow)
  const [source, setSource] = useState(spec.default_source_name)
  const [confidenceRaw, setConfidenceRaw] = useState('')
  const [signalQuality, setSignalQuality] = useState('')
  const [attempted, setAttempted] = useState(false)

  const unitSpec = metric.accepted_units.find((u) => u.unit === unit) ?? metric.accepted_units[0]
  const value = parseDecimal(valueRaw)
  const canonical = value === null ? null : toCanonical(unitSpec, value)
  const outOfRange = canonical !== null && !withinBounds(metric, canonical)
  // The most common entry error is the wrong unit (36.8 typed with °F selected, 5.5 with
  // mg/dL). Offer another accepted unit in which the same number would be in range.
  const otherUnit =
    outOfRange && value !== null
      ? metric.accepted_units.find((u) => u.unit !== unitSpec.unit && withinBounds(metric, toCanonical(u, value)))
      : undefined
  const when = timeLocal ? new Date(timeLocal) : null
  const validTime = when !== null && !Number.isNaN(when.getTime())
  const confidence = confidenceRaw.trim() === '' ? null : parseDecimal(confidenceRaw)

  const valueError =
    valueRaw.trim() === '' ? (attempted ? 'Enter the value you measured.' : null)
      : value === null ? 'Enter a number, for example 36.8.' : null
  const timeError = !validTime
    ? 'Enter the date and time of the reading.'
    : minutesAhead(when) > spec.max_future_minutes
      ? 'This time is in the future. Use the time the reading was taken.'
      : null
  const confidenceError =
    confidenceRaw.trim() !== '' && (confidence === null || confidence < 0 || confidence > 1)
      ? 'Confidence is a number from 0 to 1, for example 0.9.'
      : null
  const blocked = value === null || timeError !== null || confidenceError !== null
  const showTimeError = timeError !== null && (attempted || validTime)

  const record = useMutation({
    mutationFn: () =>
      crewApi.createEntry({
        astronaut_id: astronautId,
        metric_code: metric.code,
        value: value as number,
        unit: unitSpec.unit,
        context,
        recorded_at: (when as Date).toISOString(),
        source_name: source.trim() || spec.default_source_name,
        confidence,
        signal_quality: signalQuality || null,
      }),
    onSuccess: (r) => {
      onRecorded(r)
      setValueRaw('')
      setTimeLocal(localNow())
      setAttempted(false)
      qc.invalidateQueries()
    },
  })

  const chooseMetric = (code: string) => {
    const next = byCode.get(code)
    if (!next) return
    setMetricCode(code)
    setContext((c) => (next.contexts.includes(c) ? c : next.contexts[0]))
    setUnit(next.unit)
    writeLocal(METRIC_KEY, code)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setAttempted(true)
    if (!blocked) record.mutate()
  }

  const contextHelp = spec.contexts.find((c) => c.code === context)?.description
  const range = rangeText(metric, unitSpec)
  const utcText = validTime ? `${when.toISOString().slice(0, 16).replace('T', ' ')} UTC` : null
  const id = (name: string) => `${ids}-${name}`

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <FormRow id={id('metric')} label="Indicator">
        <select id={id('metric')} className={inputCls} value={metric.code} onChange={(e) => chooseMetric(e.target.value)}>
          {spec.categories.map((cat) => (
            <optgroup key={cat.code} label={cat.label}>
              {spec.metrics.filter((m) => m.category === cat.code).map((m) => (
                <option key={m.code} value={m.code}>{m.display_name}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </FormRow>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormRow
          id={id('context')}
          label="Context"
          notes={contextHelp && <span className="text-ink-faint">{contextHelp}</span>}
        >
          <select
            id={id('context')}
            className={inputCls}
            value={context}
            onChange={(e) => setContext(e.target.value)}
            aria-describedby={`${id('context')}-notes`}
          >
            {metric.contexts.map((c) => <option key={c} value={c}>{contextName(spec, c)}</option>)}
          </select>
        </FormRow>

        <FormRow
          id={id('time')}
          label="Time of reading (your local time)"
          notes={
            showTimeError
              ? <span role="alert" className="text-sev-critical">{timeError}</span>
              : utcText && <span className="num text-ink-faint">Stored as {utcText}</span>
          }
        >
          <div className="flex gap-2">
            <input
              id={id('time')}
              type="datetime-local"
              className={`${fieldCls} num min-w-0 flex-1`}
              value={timeLocal}
              onChange={(e) => setTimeLocal(e.target.value)}
              aria-invalid={showTimeError}
              aria-describedby={`${id('time')}-notes`}
            />
            <Button type="button" variant="quiet" onClick={() => setTimeLocal(localNow())}>Now</Button>
          </div>
        </FormRow>
      </div>

      <FormRow
        id={id('value')}
        label="Value"
        notes={
          <>
            {valueError && <span role="alert" className="text-sev-critical">{valueError}</span>}
            {!unitSpec.canonical && canonical !== null && (
              <span className="text-ink">
                Stored as <span className="num">{fmtValue(canonical)} {metric.unit_label}</span>
                <span className="text-ink-faint"> ({unitSpec.note})</span>
              </span>
            )}
            {outOfRange && (
              <span role="alert" className="text-sev-watch">
                Outside the accepted range. AM will store this entry as rejected. Check the value and the unit.
              </span>
            )}
            {otherUnit && value !== null && (
              <span className="flex flex-wrap items-center gap-x-2 text-ink">
                <span>
                  <span className="num">{fmtValue(value)} {otherUnit.label}</span>
                  {!otherUnit.canonical && (
                    <span className="num"> ({fmtValue(toCanonical(otherUnit, value))} {metric.unit_label})</span>
                  )}{' '}
                  would be in range.
                </span>
                <button
                  type="button"
                  className="text-signal underline underline-offset-2 hover:text-ink"
                  onClick={() => setUnit(otherUnit.unit)}
                >
                  Use {otherUnit.label}
                </button>
              </span>
            )}
            {range && (
              <span className="text-ink-faint">
                Accepted range {range}. An engineering check for entry errors, not a clinical range.
              </span>
            )}
          </>
        }
      >
        <div className="flex gap-2">
          <input
            id={id('value')}
            className={`${fieldCls} num min-w-0 flex-1`}
            inputMode="decimal"
            autoComplete="off"
            value={valueRaw}
            onChange={(e) => setValueRaw(e.target.value)}
            aria-invalid={valueError !== null}
            aria-describedby={`${id('value')}-notes`}
          />
          <select
            aria-label="Unit"
            className={`${inputCls} w-36 shrink-0`}
            value={unitSpec.unit}
            onChange={(e) => setUnit(e.target.value)}
          >
            {metric.accepted_units.map((u) => <option key={u.unit} value={u.unit}>{u.label}</option>)}
          </select>
        </div>
      </FormRow>

      <FormRow
        id={id('source')}
        label="Source"
        notes={<span className="text-ink-faint">The device you read it from, or leave as {spec.default_source_name}.</span>}
      >
        <input
          id={id('source')}
          className={inputCls}
          maxLength={128}
          value={source}
          onChange={(e) => setSource(e.target.value)}
          aria-describedby={`${id('source')}-notes`}
        />
      </FormRow>

      <details className="rounded-md border border-seam px-3 py-2 text-sm">
        <summary className="cursor-pointer text-ink-dim">Optional: confidence and signal quality</summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <FormRow
            id={id('confidence')}
            label="Confidence (0 to 1)"
            notes={
              confidenceError
                ? <span role="alert" className="text-sev-critical">{confidenceError}</span>
                : <span className="text-ink-faint">Below {spec.degraded_confidence_below} the reading is kept as degraded.</span>
            }
          >
            <input
              id={id('confidence')}
              className={`${fieldCls} num`}
              inputMode="decimal"
              autoComplete="off"
              value={confidenceRaw}
              onChange={(e) => setConfidenceRaw(e.target.value)}
              aria-invalid={confidenceError !== null}
              aria-describedby={`${id('confidence')}-notes`}
            />
          </FormRow>
          <FormRow
            id={id('signal')}
            label="Signal quality"
            notes={<span className="text-ink-faint">Poor signal quality keeps the reading as degraded.</span>}
          >
            <select
              id={id('signal')}
              className={inputCls}
              value={signalQuality}
              onChange={(e) => setSignalQuality(e.target.value)}
              aria-describedby={`${id('signal')}-notes`}
            >
              <option value="">Not stated</option>
              <option value="good">Good</option>
              <option value="poor">Poor</option>
            </select>
          </FormRow>
        </div>
      </details>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={record.isPending}>
          {record.isPending ? 'Recording…' : 'Record measurement'}
        </Button>
        {attempted && blocked && <span className="text-sm text-ink-dim">Fix the highlighted fields first.</span>}
      </div>
      {record.error && <ErrorNote error={record.error} />}
    </form>
  )
}

// ------------------------------------------------------------------ result
function AssessmentBlock({ r, metric }: { r: ManualEntryResult; metric: EntryMetric | undefined }) {
  const a = r.assessment
  if (a.status === 'skipped_rejected') {
    return <p className="text-sm text-ink-dim">Not assessed, because the entry was rejected.</p>
  }
  if (a.status === 'no_baseline') {
    if (metric && metric.alert_rule_id === null) {
      return (
        <p className="text-sm text-ink-dim">
          Logged only: this indicator has no baseline and no alert rule.
          {metric.code === RADIATION && ' AM keeps a running total and does not evaluate exposure limits.'}
        </p>
      )
    }
    return (
      <div className="flex flex-col gap-1 text-sm">
        <p className="text-ink-dim">
          Not compared yet: there is no personal baseline for this context. AM forms one from your good-quality
          readings and never compares a reading with a different context.
        </p>
        {a.reasons.map((x) => <p key={x} className="text-xs text-ink-faint">{x}</p>)}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2 text-sm">
      <KeyValue
        items={[
          ['Result', <SeverityTag severity={a.severity} />],
          ['Deviation', (
            <span>
              <span className="num">{fmtZ(a.z)}</span>
              <span className="text-ink-dim"> from your personal median, in units of your personal spread (robust z)</span>
            </span>
          )],
          ...(a.instant_severity && a.instant_severity !== a.severity
            ? [['Single-reading level', (
                <span className="text-ink-dim">
                  <SeverityTag severity={a.instant_severity} /> before the persistence rule ({a.persistence_count} recent
                  deviation{a.persistence_count === 1 ? '' : 's'} in the same direction)
                </span>
              )] as [ReactNode, ReactNode]]
            : []),
        ]}
      />
      <ul className="flex flex-col gap-0.5 text-xs text-ink-faint">
        {a.reasons.map((x) => <li key={x}>{x}</li>)}
      </ul>
      <DemoThresholdNote label={r.threshold_label} />
    </div>
  )
}

function baselineText(action: string, reason: string): string | null {
  if (action === 'created') return `A personal baseline was created (${reason}).`
  if (action === 'frozen') return `The baseline was frozen pending review (${reason}).`
  if (action === 'adapted' || action === 'capped') return `The baseline was updated (${reason}).`
  return null
}

function EntryResultView({ r, spec }: { r: ManualEntryResult; spec: EntrySpec }) {
  const e = r.entry
  const metric = spec.metrics.find((m) => m.code === e.metric_code)
  const conv = e.conversion
  const notes = e.validation_notes.map(describeNote)
  const boundIssue = e.validation_notes.some((n) => n.includes('_sanity_bound'))
  const baseline = r.baseline_event && baselineText(r.baseline_event.action, r.baseline_event.reason)
  return (
    <div className="flex flex-col gap-5">
      {r.accepted ? (
        <div>
          <p className={`text-lg font-medium ${e.quality === 'degraded' ? 'text-sev-watch' : 'text-sev-normal'}`}>
            {e.quality === 'degraded' ? 'Accepted, marked as degraded quality' : 'Accepted'}
          </p>
          {notes.length > 0 && (
            <ul className="mt-1 flex flex-col gap-0.5 text-sm text-ink-dim">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
          )}
        </div>
      ) : (
        <div className="rounded-md border border-sev-critical/40 bg-sev-critical/10 px-4 py-3 text-sm">
          <p className="font-medium text-ink">Rejected. The entry is kept with these reasons:</p>
          <ul className="mt-1 list-disc pl-5 text-ink">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
          {boundIssue && <p className="mt-2 text-xs text-ink-dim">{BOUND_TEXT} {metric?.bound_note}</p>}
        </div>
      )}

      <KeyValue
        items={[
          ['Indicator', `${e.display_name}, ${contextName(spec, e.context).toLowerCase()}`],
          ...(conv
            ? [
                ['Entered', <span className="num">{fmtValue(conv.entered_value)} {conv.entered_label}</span>],
                ['Stored as', (
                  <span>
                    <span className="num">{fmtValue(conv.canonical_value)} {conv.canonical_label}</span>
                    <span className="block text-xs text-ink-faint">{conv.formula}. {conv.source}</span>
                  </span>
                )],
              ] as [ReactNode, ReactNode][]
            : [['Value', <span className="num">{fmtValue(e.value)} {e.unit_label}</span>] as [ReactNode, ReactNode]]),
          ['Recorded', <span className="num">{fmtDateTime(e.recorded_at)}</span>],
          ['Source', e.source_name],
          ['Reading', <span className="num">#{e.id}</span>],
        ]}
      />

      <div>
        <h3 className="mb-2 text-sm font-semibold text-ink">Assessment</h3>
        <AssessmentBlock r={r} metric={metric} />
      </div>

      {r.alert && (
        <p className="text-sm">
          <Link to={`/alerts?id=${r.alert.id}&filter=all`} className="text-signal hover:underline">
            Alert #{r.alert.id}
          </Link>{' '}
          <SeverityTag severity={r.alert.severity} />{' '}
          <span className="text-ink-dim">({r.alert.status}). This reading is part of its evidence.</span>
        </p>
      )}
      {baseline && <p className="text-sm text-ink-dim">{baseline}</p>}
    </div>
  )
}

// ------------------------------------------------------------------ lists
function resultLabel(row: ManualEntryRow): { text: string; cls: string } {
  if (!row.accepted) return { text: 'Rejected', cls: 'text-sev-critical' }
  if (row.quality === 'degraded') return { text: 'Degraded', cls: 'text-sev-watch' }
  return { text: 'Accepted', cls: 'text-sev-normal' }
}

function RecentEntries({ astronautId, spec }: { astronautId: number; spec: EntrySpec }) {
  const q = useQuery({
    queryKey: ['manual-entries', astronautId],
    queryFn: () => crewApi.recentEntries(astronautId, 10),
  })
  return (
    <Section title="Your last 10 manual entries">
      {q.isError ? <ErrorNote error={q.error} /> : !q.data ? <Loading /> : q.data.length === 0 ? (
        <p className="text-sm text-ink-dim">No manual entries yet. Readings you record here appear in this list.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-ink-dim">
              <tr>
                <th className="pb-1 font-normal">Recorded</th>
                <th className="pb-1 font-normal">Indicator</th>
                <th className="pb-1 text-right font-normal">Value</th>
                <th className="pb-1 pl-4 font-normal">Entry</th>
                <th className="pb-1 font-normal">Assessment</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((row) => {
                const label = resultLabel(row)
                return (
                  <tr key={row.id} className="border-t border-seam/60 align-top">
                    <td className="num py-1.5 pr-3 text-ink-dim">{fmtDateTime(row.recorded_at)}</td>
                    <td className="py-1.5 pr-3">
                      {row.display_name}
                      <span className="block text-xs text-ink-faint">{contextName(spec, row.context)}</span>
                    </td>
                    <td className="num py-1.5 text-right">
                      {fmtValue(row.value)} {row.unit_label}
                      {row.conversion && (
                        <span className="block text-xs text-ink-faint">
                          entered {fmtValue(row.conversion.entered_value)} {row.conversion.entered_label}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pl-4 pr-3">
                      <span className={label.cls}>{label.text}</span>
                      {!row.accepted && row.validation_notes[0] && (
                        <span className="block text-xs text-ink-faint">{describeNote(row.validation_notes[0])}</span>
                      )}
                    </td>
                    <td className="py-1.5">
                      {row.assessment_status === 'assessed' ? (
                        <span className="inline-flex flex-wrap items-center gap-x-2">
                          <SeverityTag severity={row.severity} />
                          <span className="num text-xs text-ink-dim">{fmtZ(row.deviation_score)}</span>
                          {row.alert_id && (
                            <Link to={`/alerts?id=${row.alert_id}&filter=all`} className="num text-xs text-signal hover:underline">
                              alert #{row.alert_id}
                            </Link>
                          )}
                        </span>
                      ) : (
                        <span className="text-xs text-ink-faint">
                          {row.assessment_status === 'no_baseline' ? 'Not compared' : 'Not assessed'}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

function RadiationLog({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['radiation', astronautId], queryFn: () => crewApi.radiation(astronautId) })
  const data = q.data
  const recent = data ? [...data.days].reverse().slice(0, 7) : []
  return (
    <Section title="Radiation dose log" aside={data?.simulated ? <SimulatedMark /> : undefined}>
      {q.isError ? <ErrorNote error={q.error} /> : !data ? <Loading /> : (
        <div className="flex flex-col gap-3 text-sm">
          {data.entries === 0 ? (
            <p className="text-ink-dim">No dosimeter readings yet. Choose Radiation dose in the form to log one.</p>
          ) : (
            <>
              <KeyValue
                items={[
                  ['Total logged', <span className="num">{fmtValue(data.total)} {data.unit}</span>],
                  ['Days with readings', <span className="num">{data.days.length}</span>],
                ]}
              />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-ink-dim">
                    <tr>
                      <th className="pb-1 font-normal">Date (UTC)</th>
                      <th className="pb-1 text-right font-normal">Mission day</th>
                      <th className="pb-1 text-right font-normal">Day dose</th>
                      <th className="pb-1 text-right font-normal">Running total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((d) => (
                      <tr key={d.utc_date} className="border-t border-seam/60">
                        <td className="num py-1.5 text-ink-dim">{d.utc_date}</td>
                        <td className="num py-1.5 text-right">{d.mission_day ?? '–'}</td>
                        <td className="num py-1.5 text-right">
                          {fmtValue(d.dose)}
                          {d.entries > 1 && <span className="text-xs text-ink-faint"> ({d.entries} entries)</span>}
                        </td>
                        <td className="num py-1.5 text-right">{fmtValue(d.cumulative)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-ink-faint">Doses in {data.unit}, latest 7 days with readings.</p>
            </>
          )}
          <p className="text-xs text-ink-dim">{data.note} It adds up what the dosimeter reported.</p>
        </div>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ page
function Workbench({ spec, astronautId }: { spec: EntrySpec; astronautId: number }) {
  const [result, setResult] = useState<ManualEntryResult | null>(null)
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <Section title="New entry">
        <EntryForm spec={spec} astronautId={astronautId} onRecorded={setResult} />
      </Section>
      <Section title="Result">
        <div aria-live="polite">
          {result ? (
            <EntryResultView r={result} spec={spec} />
          ) : (
            <p className="text-sm text-ink-dim">
              After you record a reading, this shows whether it was accepted, any unit conversion, and how it compares
              with your own baseline.
            </p>
          )}
        </div>
      </Section>
    </div>
  )
}

export function RecordPage() {
  const { astronaut, astronautsLoading } = useSession()
  const spec = useQuery({ queryKey: ['entry-spec'], queryFn: crewApi.entrySpec, staleTime: Infinity })

  if (!astronaut) return astronautsLoading ? <Loading /> : <NoCrew />
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold">Record a measurement</h1>
        <p className="mt-1 max-w-[80ch] text-sm text-ink-dim">
          For {astronaut.display_name} ({astronaut.code}). Readings you enter go through the same checks as sensor
          data: unit conversion, an engineering sanity check, and comparison with your own baseline in the same
          context.
        </p>
        {astronaut.is_simulated && (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink-dim">
            <SimulatedMark /> This crew record is simulated. Entries you add are stored as manual entries next to the
            generated data.
          </p>
        )}
      </div>
      {spec.isError ? <ErrorNote error={spec.error} /> : !spec.data ? <Loading /> : (
        <>
          <Workbench key={astronaut.id} spec={spec.data} astronautId={astronaut.id} />
          <div className="grid gap-8 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
            <RecentEntries astronautId={astronaut.id} spec={spec.data} />
            <RadiationLog astronautId={astronaut.id} />
          </div>
        </>
      )}
    </div>
  )
}
