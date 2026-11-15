import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type AlertDetail } from '../api/client'
import { invalidateGuidance } from '../api/guidance'
import { AlertGuidance } from '../components/Guidance'
import {
  Button,
  DemoThresholdNote,
  Empty,
  ErrorNote,
  Field,
  inputCls,
  KeyValue,
  Loading,
  Section,
  SeverityTag,
  SimulatedMark,
} from '../components/ui'
import { contextLabel, fmtAgo, fmtDateTime, fmtNum, fmtZ, severityColor, titleCase, unitLabel } from '../lib/format'
import { useAuth } from '../state/auth'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const FILTERS = [
  { key: 'open', label: 'Open' },
  { key: 'resolved', label: 'Resolved' },
  { key: 'all', label: 'All' },
] as const

function AlertActions({ alert }: { alert: AlertDetail }) {
  const { operator, setOperator } = useSession()
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const done = () => {
    setNote('')
    qc.invalidateQueries({ queryKey: ['alerts'] })
    qc.invalidateQueries({ queryKey: ['alert', alert.id] })
    qc.invalidateQueries({ queryKey: ['overview'] })
    invalidateGuidance(qc) // review state changes the level an alert counts at
  }
  const ack = useMutation({ mutationFn: () => api.acknowledge(alert.id, operator.trim(), note.trim()), onSuccess: done })
  const resolve = useMutation({ mutationFn: () => api.resolve(alert.id, operator.trim(), note.trim()), onSuccess: done })

  if (alert.status === 'resolved') return null
  const busy = ack.isPending || resolve.isPending
  const hasOperator = operator.trim().length > 0
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => e.preventDefault()}>
      <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
        <Field label="Your name">
          <input className={inputCls} value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="e.g. Flight surgeon" />
        </Field>
        <Field label="Note (required to resolve)">
          <input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was checked or decided" />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        {alert.status === 'active' && (
          <Button variant="quiet" disabled={!hasOperator || busy} onClick={() => ack.mutate()}>
            Acknowledge
          </Button>
        )}
        <Button disabled={!hasOperator || note.trim().length < 3 || busy} onClick={() => resolve.mutate()}>
          Resolve alert
        </Button>
      </div>
      {(ack.error || resolve.error) && <ErrorNote error={ack.error ?? resolve.error} />}
      <p className="text-xs text-ink-faint">
        Alerts are never closed automatically. Each acknowledgement and resolution is stored with your name.
      </p>
    </form>
  )
}

function AlertDetailView({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['alert', id], queryFn: () => api.alert(id), refetchInterval: 15_000 })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading />
  const a = q.data
  const evidence = [...a.evidence].reverse()
  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <SeverityTag severity={a.severity} size="lg" />
          <span className="text-ink-dim">peak {a.peak_severity.toLowerCase()}</span>
          {a.is_simulated && <SimulatedMark />}
        </div>
        <h2 className="mt-1 text-xl font-semibold">
          {titleCase(a.metric_code.replaceAll('_', ' '))}, {contextLabel(a.context).toLowerCase()}, {a.direction} deviation
        </h2>
      </div>

      <AlertGuidance alertId={a.id} />

      <KeyValue
        items={[
          ['Status', titleCase(a.status)],
          ['First seen', <span className="num">{fmtDateTime(a.first_seen_at)}</span>],
          ['Last seen', <span className="num">{fmtDateTime(a.last_seen_at)} ({fmtAgo(a.last_seen_at)})</span>],
          ['Deviation', <span className="num">last {fmtZ(a.last_deviation)}, peak {fmtZ(a.peak_deviation)}</span>],
          ['Persistence', <span className="num">{a.persistence_count} recent deviating readings</span>],
          ['Rule', <span className="num">{a.rule_id} ({a.threshold_version})</span>],
          ['Thresholds', <DemoThresholdNote label={a.threshold_label} />],
        ]}
      />

      <div>
        <h3 className="mb-1 text-sm font-medium text-ink-dim">How AMC reached this result</h3>
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
          {a.explanation.split('; ').map((line, i) => <li key={i}>{line}</li>)}
        </ul>
      </div>

      <AlertActions alert={a} />

      <Section title="Supporting measurements" aside={`${a.evidence.length} readings`}>
        <table className="w-full text-sm">
          <thead className="text-left text-ink-dim">
            <tr>
              <th className="pb-1 font-normal">Recorded</th>
              <th className="pb-1 text-right font-normal">Value</th>
              <th className="pb-1 text-right font-normal">Deviation</th>
              <th className="pb-1 pl-4 font-normal">Severity then</th>
              <th className="hidden pb-1 font-normal sm:table-cell">Quality</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((e) => (
              <tr key={e.measurement_id} className="border-t border-seam/60">
                <td className="num py-1.5 text-ink-dim">{fmtDateTime(e.measurement.recorded_at)}</td>
                <td className="num py-1.5 text-right">
                  {fmtNum(e.measurement.value)} <span className="text-ink-dim">{unitLabel(e.measurement.unit)}</span>
                </td>
                <td className="num py-1.5 text-right" style={{ color: severityColor(e.severity_at_time) }}>{fmtZ(e.deviation)}</td>
                <td className="py-1.5 pl-4"><SeverityTag severity={e.severity_at_time} /></td>
                <td className="hidden py-1.5 text-ink-dim sm:table-cell">{e.measurement.quality}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {a.acks.length > 0 && (
        <Section title="Review history">
          <ul className="space-y-2 text-sm">
            {a.acks.map((k, i) => (
              <li key={i}>
                <span className="num text-ink-dim">{fmtDateTime(k.created_at)}</span>{' '}
                <span className="font-medium">{k.actor}</span> {k.action === 'resolve' ? 'resolved' : 'acknowledged'}
                {k.note && <span className="text-ink-dim">: {k.note}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

export function AlertsPage() {
  const { astronaut } = useSession()
  const { isDoctor } = useAuth()
  // The doctor's Alert center covers the whole crew; an astronaut sees only their own alerts.
  const scope = isDoctor ? undefined : astronaut?.id
  const [params, setParams] = useSearchParams()
  const filter = FILTERS.find((f) => f.key === params.get('filter'))?.key ?? 'open'
  const selected = params.get('id') ? Number(params.get('id')) : null

  const list = useQuery({
    queryKey: ['alerts', scope ?? 'crew', filter],
    queryFn: () =>
      api.alerts({
        astronaut_id: scope,
        open_only: filter === 'open' ? true : undefined,
        status: filter === 'resolved' ? 'resolved' : undefined,
      }),
    enabled: isDoctor || !!astronaut,
    refetchInterval: 15_000,
  })

  if (!isDoctor && !astronaut) return <NoCrew />
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params)
    if (v === null) next.delete(k)
    else next.set(k, v)
    setParams(next, { replace: true })
  }
  const alerts = list.data ?? []
  const activeId = selected ?? alerts[0]?.id ?? null

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4">
        <h1 className="mr-auto text-2xl font-semibold">Alert center</h1>
        <div role="radiogroup" aria-label="Filter" className="flex rounded-md border border-seam-strong p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              role="radio"
              aria-checked={f.key === filter}
              onClick={() => { set('filter', f.key); set('id', null) }}
              className={`rounded px-3 py-1 text-sm ${f.key === filter ? 'bg-deck text-ink' : 'text-ink-dim hover:text-ink'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {list.isError ? (
        <ErrorNote error={list.error} />
      ) : list.isLoading ? (
        <Loading />
      ) : alerts.length === 0 ? (
        <Empty title={filter === 'open' ? 'No open alerts' : 'No alerts in this view'}>
          Alerts open automatically when readings deviate persistently from the personal baseline.
        </Empty>
      ) : (
        <div className="grid gap-8 lg:grid-cols-[340px_minmax(0,1fr)]">
          <ul aria-label="Alerts" className="flex flex-col border-t border-seam">
            {alerts.map((a) => (
              <li key={a.id}>
                <button
                  onClick={() => set('id', String(a.id))}
                  aria-current={a.id === activeId}
                  className={`w-full border-b border-seam/60 px-3 py-2.5 text-left transition-colors ${
                    a.id === activeId ? 'bg-deck shadow-[inset_2px_0_0_var(--color-signal)]' : 'hover:bg-deck/50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <SeverityTag severity={a.severity} />
                    <span className="text-xs text-ink-faint">{fmtAgo(a.last_seen_at)}</span>
                  </div>
                  <p className="mt-0.5 text-sm">
                    {titleCase(a.metric_code.replaceAll('_', ' '))}, {contextLabel(a.context).toLowerCase()}
                  </p>
                  <p className="text-xs text-ink-dim">
                    {titleCase(a.status)}, peak <span className="num">{fmtZ(a.peak_deviation)}</span>
                  </p>
                </button>
              </li>
            ))}
          </ul>
          <AnimatePresence mode="wait">
            {activeId != null && (
              <motion.div
                key={activeId}
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              >
                <AlertDetailView id={activeId} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}
