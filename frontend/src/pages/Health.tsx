import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { BaselineBandChart } from '../components/deviation'
import { DemoThresholdNote, Empty, ErrorNote, KeyValue, Loading, Section, SeverityTag } from '../components/ui'
import { contextLabel, fmtDateTime, fmtNum, fmtZ, parseUtc, severityColor, unitLabel } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const RANGES = [
  { key: '24h', label: '24 h', hours: 24 },
  { key: '7d', label: '7 days', hours: 24 * 7 },
  { key: '30d', label: '30 days', hours: 24 * 30 },
  { key: 'all', label: 'All', hours: null },
] as const

export function HealthPage() {
  const { astronaut } = useSession()
  const [params, setParams] = useSearchParams()
  const overview = useQuery({
    queryKey: ['overview', astronaut?.id],
    queryFn: () => api.overview(astronaut!.id),
    enabled: !!astronaut,
  })
  const thresholds = useQuery({ queryKey: ['thresholds'], queryFn: api.thresholds })

  const streams = overview.data?.latest ?? []
  const metric = params.get('metric') ?? streams[0]?.metric_code
  const context = params.get('context') ?? streams[0]?.context
  const range = RANGES.find((r) => r.key === params.get('range')) ?? RANGES[1]
  const stream = streams.find((s) => s.metric_code === metric && s.context === context)

  // Anchor the window on the latest reading, so older simulated missions still render.
  const since = useMemo(() => {
    if (!range.hours || !stream) return undefined
    const last = parseUtc(stream.measurement.recorded_at).getTime()
    return new Date(last - range.hours * 3600_000).toISOString()
  }, [range.hours, stream])

  const series = useQuery({
    queryKey: ['measurements', astronaut?.id, metric, context, since],
    queryFn: () => api.measurements({ astronaut_id: astronaut!.id, metric_code: metric, context, since, limit: 5000 }),
    enabled: !!astronaut && !!metric && !!context,
    refetchInterval: 30_000,
  })

  if (!astronaut) return <NoCrew />
  if (overview.isError) return <ErrorNote error={overview.error} />
  if (!overview.data) return <Loading />
  if (!streams.length) return <Empty title="No readings recorded">Ingest measurements to see trends here.</Empty>

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    next.set(k, v)
    setParams(next, { replace: true })
  }
  const rule = thresholds.data?.rules.find((r) => r.metric_code === metric)
  const data = series.data ?? []
  const flagged = data.filter((m) => m.severity && m.severity !== 'NORMAL').slice(-12).reverse()
  const baseline = stream?.baseline

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4">
        <h1 className="mr-auto text-2xl font-semibold">Health monitoring</h1>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-dim">Indicator</span>
          <select
            value={`${metric}|${context}`}
            onChange={(e) => {
              const [m, c] = e.target.value.split('|')
              const next = new URLSearchParams(params)
              next.set('metric', m)
              next.set('context', c)
              setParams(next, { replace: true })
            }}
            className="rounded-md border border-seam-strong bg-deck px-2.5 py-1.5 text-ink focus:border-signal focus:outline-none"
          >
            {streams.map((s) => (
              <option key={`${s.metric_code}|${s.context}`} value={`${s.metric_code}|${s.context}`}>
                {s.display_name}, {contextLabel(s.context).toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <div role="radiogroup" aria-label="Time range" className="flex rounded-md border border-seam-strong p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.key}
              role="radio"
              aria-checked={r.key === range.key}
              onClick={() => set('range', r.key)}
              className={`rounded px-3 py-1 text-sm ${r.key === range.key ? 'bg-deck text-ink' : 'text-ink-dim hover:text-ink'}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <Section
        title={stream ? `${stream.display_name}, ${contextLabel(stream.context).toLowerCase()}` : 'Indicator'}
        aside={rule && thresholds.data ? <DemoThresholdNote label={thresholds.data.label} /> : 'No alert rule for this indicator'}
      >
        {series.isError ? (
          <ErrorNote error={series.error} />
        ) : series.isLoading ? (
          <Loading />
        ) : data.length === 0 ? (
          <Empty title="No readings in this range" />
        ) : (
          <BaselineBandChart measurements={data} baseline={baseline} rule={rule} />
        )}
        <p className="mt-2 text-xs text-ink-faint">
          Dashed line: personal median. Inner band: within the watch threshold. Outer band: within the warning
          threshold. Bands come from the active baseline only; earlier readings were assessed against the baseline
          version current at the time.
        </p>
      </Section>

      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Active baseline">
          {baseline ? (
            <KeyValue
              items={[
                ['Version', <span className="num">v{baseline.version} ({baseline.status}{baseline.is_frozen ? ', frozen' : ''})</span>],
                ['Median', <span className="num">{fmtNum(baseline.median)} {unitLabel(stream!.measurement.unit)}</span>],
                ['Deviation scale', <span className="num">{fmtNum(baseline.deviation_scale, 2)}</span>],
                ['5th to 95th percentile', <span className="num">{fmtNum(baseline.p05)} to {fmtNum(baseline.p95)}</span>],
                ['Samples', <span className="num">{baseline.n}</span>],
                ['Window', <span className="num">{fmtDateTime(baseline.window_start)} to {fmtDateTime(baseline.window_end)}</span>],
              ]}
            />
          ) : (
            <p className="text-sm text-ink-dim">
              Not enough good-quality readings in this context to form a provisional baseline yet.
            </p>
          )}
        </Section>
        <Section title="Recent flagged readings" aside={`${flagged.length} shown`}>
          {flagged.length === 0 ? (
            <p className="text-sm text-ink-dim">No readings outside the normal band in this range.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-ink-dim">
                <tr><th className="pb-1 font-normal">Recorded</th><th className="pb-1 text-right font-normal">Value</th><th className="pb-1 text-right font-normal">Deviation</th><th className="pb-1 pl-4 font-normal">Result</th></tr>
              </thead>
              <tbody>
                {flagged.map((m) => (
                  <tr key={m.id} className="border-t border-seam/60">
                    <td className="num py-1.5 text-ink-dim">{fmtDateTime(m.recorded_at)}</td>
                    <td className="num py-1.5 text-right">{fmtNum(m.value)}</td>
                    <td className="num py-1.5 text-right" style={{ color: severityColor(m.severity) }}>{fmtZ(m.deviation_score)}</td>
                    <td className="py-1.5 pl-4"><SeverityTag severity={m.severity} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
    </div>
  )
}
