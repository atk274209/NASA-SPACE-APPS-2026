// Module P, doctor side: live cabin readings against the envelope, autonomy mode, manual override,
// the timeline of every action with the comfort signal behind it, and environment alerts.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { cabinApi, VAR_LABEL, type Control } from '../api/cabin'
import { ErrorNote, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime, severityColor, unitLabel } from '../lib/format'

function useOverviewMutation<T>(fn: (x: T) => Promise<unknown>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ['cabin-overview'] }) })
}

function ControlRow({ c }: { c: Control }) {
  const [value, setValue] = useState(String(c.setpoint))
  const [reason, setReason] = useState('')
  const set = useOverviewMutation(() => cabinApi.set(c.id, Number(value), reason))
  const mode = useOverviewMutation((m: Control['mode']) => cabinApi.mode(c.id, m, 'set by the medical officer'))
  const outside = c.reading != null && (c.reading < (c.variable === 'pressure' ? c.auto_min : c.min) || c.reading > (c.variable === 'pressure' ? c.auto_max : c.max))
  const needsReason = c.variable === 'pressure' && (Number(value) < c.auto_min || Number(value) > c.auto_max)
  return (
    <tr className="border-t border-seam align-top">
      <td className="py-2 pr-3">{c.zone_name}</td>
      <td className="py-2 pr-3">{VAR_LABEL[c.variable]}</td>
      <td className="num py-2 pr-3" style={{ color: outside ? 'var(--color-sev-critical)' : undefined }}>
        {c.reading ?? '—'} {unitLabel(c.unit)}
        <span className="block text-xs text-ink-faint">set {c.setpoint} · band {c.auto_min}–{c.auto_max} · hard {c.min}–{c.max}</span>
        {c.fault !== 'none' && <span className="block text-xs text-sev-critical">{c.fault} fault</span>}
      </td>
      <td className="py-2 pr-3">
        <span className={c.mode === 'AUTONOMOUS' ? 'text-sev-normal' : 'text-sev-watch'}>{c.mode}</span>
        {c.mode_reason && <span className="block text-xs text-ink-faint">{c.mode_reason}</span>}
        <div className="mt-1 flex gap-2 text-xs">
          {c.mode !== 'PAUSED' && <button className="text-signal hover:underline" onClick={() => mode.mutate('PAUSED')}>Pause</button>}
          {c.mode !== 'AUTONOMOUS' && <button className="text-signal hover:underline" onClick={() => mode.mutate('AUTONOMOUS')}>Resume AM</button>}
        </div>
      </td>
      <td className="py-2">
        <form className="flex flex-wrap gap-1" onSubmit={(e) => { e.preventDefault(); set.mutate(undefined) }}>
          <input className="num h-8 w-20 rounded border border-seam-strong bg-deck px-2 text-sm" value={value} onChange={(e) => setValue(e.target.value)} aria-label="New value" />
          {needsReason && <input className="h-8 w-56 rounded border border-sev-watch bg-deck px-2 text-sm" placeholder="Approval reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />}
          <button className="h-8 rounded bg-ink px-3 text-sm font-semibold text-void disabled:opacity-50" disabled={set.isPending || Number.isNaN(Number(value)) || (needsReason && !reason.trim())}>Set</button>
        </form>
        {(set.error || mode.error) && <ErrorNote error={set.error ?? mode.error} />}
      </td>
    </tr>
  )
}

export function EnvironmentPage() {
  const q = useQuery({ queryKey: ['cabin-overview'], queryFn: () => cabinApi.overview(), refetchInterval: 5_000 })
  const pauseAll = useOverviewMutation((m: Control['mode']) => cabinApi.pauseAll(m, 'set for all zones by the medical officer'))
  const revert = useOverviewMutation((id: number) => cabinApi.revert(id))
  const ack = useOverviewMutation((id: number) => cabinApi.ackAlert(id))
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return <p className="text-ink-dim">Loading…</p>
  const openAlerts = q.data.alerts.filter((a) => a.status === 'open')
  return (
    <div className="grid gap-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-2xl font-semibold">Environment</h1>
        <SimulatedMark title="SIMULATED cabin: no real hardware is connected." />
        <span className="text-xs text-sev-watch">{q.data.label}</span>
        <button className="h-9 rounded-md border border-seam-strong px-3 text-sm hover:border-signal" onClick={() => pauseAll.mutate('PAUSED')}>Pause all autonomy</button>
        <button className="h-9 rounded-md border border-seam-strong px-3 text-sm hover:border-signal" onClick={() => pauseAll.mutate('AUTONOMOUS')}>Resume all</button>
      </div>
      {openAlerts.length > 0 && (
        <Section title={`Environment alerts (${openAlerts.length})`}>
          <ul className="grid gap-2">
            {openAlerts.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 rounded-md border border-seam bg-hull px-3 py-2 text-sm">
                <span style={{ color: severityColor(a.severity) }}>{a.severity}</span>
                <span className="num text-xs text-ink-faint">{a.zone_code} · {a.rule_id}</span>
                <span className="flex-1 text-ink">{a.text}</span>
                <button className="text-xs text-signal hover:underline" onClick={() => ack.mutate(a.id)}>Acknowledge</button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Zones">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="text-xs text-ink-faint"><tr><th>Zone</th><th>Variable</th><th>Reading</th><th>Mode</th><th>Manual override</th></tr></thead>
            <tbody>{q.data.controls.map((c) => <ControlRow key={c.id} c={c} />)}</tbody>
          </table>
        </div>
      </Section>
      <Section title="Timeline" aside="every signal, action, undo, override and refusal">
        <ul className="grid gap-1.5 text-sm">
          {q.data.actions.map((a) => (
            <li key={a.id} className="rounded bg-deck px-3 py-2">
              <span className="num text-xs text-ink-faint">{fmtDateTime(a.created_at)} · {a.zone_code} · {a.rule_id} · {a.actor}</span>
              <p className="text-ink">
                <span className="font-medium">{a.kind}</span> {VAR_LABEL[a.variable]?.toLowerCase()}
                {a.before != null && a.after != null && a.before !== a.after && <> {a.before} → {a.after}</>}
                {a.reason && <span className="text-ink-dim"> · {a.reason}</span>}
                {a.verified === false && <span className="text-sev-watch"> · not verified</span>}
                {a.feedback && <span className="text-ink-dim"> · astronaut: {a.feedback === 'helped' ? 'helped' : 'did not help'}</span>}
              </p>
              {a.signal_quote && <p className="text-xs text-ink-dim">Signal: “{a.signal_quote}”</p>}
              {['AUTO', 'MANUAL', 'EXCEPTION'].includes(a.kind) && !a.reverted && (
                <button className="text-xs text-signal hover:underline" onClick={() => revert.mutate(a.id)}>Revert</button>
              )}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
