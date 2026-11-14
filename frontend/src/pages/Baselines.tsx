import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Baseline } from '../api/client'
import { Button, Empty, ErrorNote, Field, inputCls, Loading, Section } from '../components/ui'
import { contextLabel, fmtDateTime, fmtNum, titleCase } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const key = (b: Baseline) => `${b.metric_code}|${b.context}`

function StatusText({ b }: { b: Baseline }) {
  if (b.is_frozen) return <span className="text-sev-watch">Frozen</span>
  return <span className={b.status === 'established' ? 'text-sev-normal' : 'text-ink-dim'}>{titleCase(b.status)}</span>
}

function UnfreezeForm({ b }: { b: Baseline }) {
  const { operator, setOperator } = useSession()
  const [reason, setReason] = useState('')
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => api.unfreeze(b.id, operator.trim(), reason.trim()),
    onSuccess: () => {
      setReason('')
      qc.invalidateQueries({ queryKey: ['baselines'] })
      qc.invalidateQueries({ queryKey: ['baseline-audit'] })
      qc.invalidateQueries({ queryKey: ['overview'] })
    },
  })
  return (
    <div className="rounded-md border border-sev-watch/40 bg-sev-watch/5 p-4">
      <p className="text-sm">
        <span className="font-medium text-sev-watch">Adaptation is paused.</span> {b.frozen_reason}
      </p>
      <p className="mt-1 text-xs text-ink-dim">
        While frozen, new readings are still assessed against this version, but it will not move toward them. Resume
        only after the change has been reviewed.
      </p>
      <form className="mt-3 grid gap-3 sm:grid-cols-[180px_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); m.mutate() }}>
        <Field label="Your name">
          <input className={inputCls} value={operator} onChange={(e) => setOperator(e.target.value)} />
        </Field>
        <Field label="Review reason">
          <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What was reviewed" />
        </Field>
        <Button type="submit" disabled={!operator.trim() || reason.trim().length < 3 || m.isPending}>Resume adaptation</Button>
      </form>
      {m.error && <div className="mt-2"><ErrorNote error={m.error} /></div>}
    </div>
  )
}

function Detail({ astronautId, b }: { astronautId: number; b: Baseline }) {
  const history = useQuery({
    queryKey: ['baselines', astronautId, 'history', b.metric_code],
    queryFn: () => api.baselines({ astronaut_id: astronautId, include_history: true, metric_code: b.metric_code }),
  })
  const audit = useQuery({
    queryKey: ['baseline-audit', astronautId, b.metric_code, b.context],
    queryFn: () => api.baselineAudit({ astronaut_id: astronautId, metric_code: b.metric_code, context: b.context }),
  })
  const versions = (history.data ?? []).filter((v) => v.context === b.context).reverse()
  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-xl font-semibold">
        {titleCase(b.metric_code.replaceAll('_', ' '))}, {contextLabel(b.context).toLowerCase()}
      </h2>
      {b.is_frozen && <UnfreezeForm b={b} />}
      <Section title="Versions" aside={`${versions.length} total`}>
        {history.isLoading ? <Loading /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead className="text-left text-ink-dim">
                <tr>
                  <th className="pb-1 font-normal">Version</th><th className="pb-1 font-normal">Status</th>
                  <th className="pb-1 text-right font-normal">Median</th><th className="pb-1 text-right font-normal">Scale</th>
                  <th className="pb-1 text-right font-normal">Samples</th><th className="pb-1 pl-4 font-normal">Window end</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id} className="border-t border-seam/60">
                    <td className="num py-1.5">v{v.version}{v.is_anchor && <span className="ml-1.5 font-sans text-xs text-signal" title="Reference for cumulative drift limits">anchor</span>}</td>
                    <td className="py-1.5">{v.status === 'superseded' ? <span className="text-ink-faint">Superseded</span> : <StatusText b={v} />}</td>
                    <td className="num py-1.5 text-right">{fmtNum(v.median)}</td>
                    <td className="num py-1.5 text-right text-ink-dim">{fmtNum(v.deviation_scale, 2)}</td>
                    <td className="num py-1.5 text-right text-ink-dim">{v.n}</td>
                    <td className="num py-1.5 pl-4 text-ink-dim">{fmtDateTime(v.window_end)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Change log">
        {audit.isLoading ? <Loading /> : (
          <ol className="space-y-2.5 text-sm">
            {(audit.data ?? []).map((a) => (
              <li key={a.id} className="grid gap-x-4 sm:grid-cols-[130px_1fr]">
                <span className="num text-ink-dim">{fmtDateTime(a.created_at)}</span>
                <span>
                  <span className={a.action === 'frozen' ? 'font-medium text-sev-watch' : 'font-medium'}>{titleCase(a.action)}</span>
                  <span className="text-ink-dim"> by {a.actor}. </span>
                  {a.reason}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </div>
  )
}

export function BaselinesPage() {
  const { astronaut } = useSession()
  const [selected, setSelected] = useState<string | null>(null)
  const q = useQuery({
    queryKey: ['baselines', astronaut?.id],
    queryFn: () => api.baselines({ astronaut_id: astronaut!.id }),
    enabled: !!astronaut,
  })
  if (!astronaut) return <NoCrew />
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading />
  const rows = q.data
  const current = rows.find((b) => key(b) === selected) ?? rows[0]

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Personal baselines</h1>
        <p className="mt-1 max-w-[72ch] text-sm text-ink-dim">
          Each indicator and context has its own reference built from this astronaut&apos;s readings. Provisional
          baselines come from short windows and are not clinical normal ranges. A baseline freezes instead of adapting
          when readings drift, so a sustained change cannot become the new normal unnoticed.
        </p>
      </div>
      {rows.length === 0 ? (
        <Empty title="No baselines yet">A provisional baseline forms after at least 20 good readings spanning one hour.</Empty>
      ) : (
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead className="text-left text-ink-dim">
                <tr>
                  <th className="pb-2 font-normal">Indicator</th><th className="pb-2 font-normal">Status</th>
                  <th className="pb-2 text-right font-normal">Median</th><th className="pb-2 text-right font-normal">Scale</th>
                  <th className="pb-2 text-right font-normal">Samples</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <tr
                    key={b.id}
                    onClick={() => setSelected(key(b))}
                    className={`cursor-pointer border-t border-seam/60 ${key(b) === key(current) ? 'bg-deck' : 'hover:bg-deck/50'}`}
                  >
                    <td className="py-2 pl-2">
                      <button className="text-left" onClick={() => setSelected(key(b))}>
                        {titleCase(b.metric_code.replaceAll('_', ' '))}
                        <span className="block text-xs text-ink-dim">{contextLabel(b.context)}, v{b.version}</span>
                      </button>
                    </td>
                    <td className="py-2"><StatusText b={b} /></td>
                    <td className="num py-2 text-right">{fmtNum(b.median)}</td>
                    <td className="num py-2 text-right text-ink-dim">{fmtNum(b.deviation_scale, 2)}</td>
                    <td className="num py-2 pr-2 text-right text-ink-dim">{b.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {current && <Detail astronautId={astronaut.id} b={current} />}
        </div>
      )}
    </div>
  )
}
