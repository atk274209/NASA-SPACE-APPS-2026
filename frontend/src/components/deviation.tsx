import { motion } from 'motion/react'
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Baseline, Measurement, ThresholdRule } from '../api/client'
import { fmtDateTime, fmtNum, fmtZ, parseUtc, severityColor, unitLabel } from '../lib/format'

const RANGE = 6 // the strip spans -6σ … +6σ around the personal median

/**
 * Deviation strip: where a reading sits relative to the astronaut's own baseline.
 * Shaded zones are the configured (DEMO) bands; the tick is the reading.
 */
export function DeviationStrip({
  z,
  severity,
  rule,
  height = 12,
}: {
  z: number | null | undefined
  severity: string | null | undefined
  rule: ThresholdRule | undefined
  height?: number
}) {
  const pos = (v: number) => ((Math.max(-RANGE, Math.min(RANGE, v)) + RANGE) / (2 * RANGE)) * 100
  const bands = rule?.bands ?? { WATCH: 2, WARNING: 3, CRITICAL: 4.5 }
  const zones: { from: number; to: number; sev: string }[] = []
  const sides = rule?.direction === 'high' ? [1] : rule?.direction === 'low' ? [-1] : [1, -1]
  for (const s of sides) {
    zones.push({ from: s * bands.WATCH, to: s * bands.WARNING, sev: 'WATCH' })
    zones.push({ from: s * bands.WARNING, to: s * bands.CRITICAL, sev: 'WARNING' })
    zones.push({ from: s * bands.CRITICAL, to: s * RANGE, sev: 'CRITICAL' })
  }
  return (
    <div
      className="relative w-full rounded-sm bg-deck"
      style={{ height }}
      role="img"
      aria-label={z == null ? 'No deviation available' : `Deviation ${fmtZ(z)} from personal baseline`}
    >
      {zones.map((zn, i) => {
        const a = pos(Math.min(zn.from, zn.to))
        const b = pos(Math.max(zn.from, zn.to))
        return (
          <span
            key={i}
            className="absolute inset-y-0"
            style={{ left: `${a}%`, width: `${b - a}%`, background: severityColor(zn.sev), opacity: 0.16 }}
          />
        )
      })}
      <span className="absolute inset-y-0 left-1/2 w-px bg-ink-faint" />
      {z != null && (
        <motion.span
          className="absolute -inset-y-1 w-[3px] rounded-full"
          style={{ background: severityColor(severity ?? 'NORMAL') }}
          initial={false}
          animate={{ left: `calc(${pos(z)}% - 1.5px)` }}
          transition={{ type: 'spring', stiffness: 220, damping: 26 }}
        />
      )}
    </div>
  )
}

/**
 * Time-series of readings drawn inside the personal baseline bands
 * (median ± WATCH·scale and ± WARNING·scale of the active baseline).
 */
export function BaselineBandChart({
  measurements,
  baseline,
  rule,
  height = 320,
}: {
  measurements: Measurement[]
  baseline: Baseline | null | undefined
  rule: ThresholdRule | undefined
  height?: number
}) {
  const data = measurements.map((m) => ({
    t: parseUtc(m.recorded_at).getTime(),
    v: m.value,
    sev: m.severity ?? 'UNASSESSED',
    z: m.deviation_score,
    id: m.id,
    sim: m.is_simulated,
    unit: m.unit,
  }))
  const scale = baseline?.deviation_scale ?? null
  const med = baseline?.median ?? null
  const b = rule?.bands
  const band = (k: 'WATCH' | 'WARNING', s: 1 | -1) => (med != null && scale && b ? med + s * b[k] * scale : null)

  const values = data.map((d) => d.v)
  const extra = [band('WARNING', 1), band('WARNING', -1)].filter((x): x is number => x != null)
  const lo = Math.min(...values, ...extra)
  const hi = Math.max(...values, ...extra)
  const pad = (hi - lo) * 0.08 || 1

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--color-seam)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(t: number) => fmtDateTime(new Date(t).toISOString())}
            stroke="var(--color-ink-faint)"
            tick={{ fill: 'var(--color-ink-dim)', fontSize: 12 }}
            minTickGap={48}
          />
          <YAxis
            domain={[lo - pad, hi + pad]}
            stroke="var(--color-ink-faint)"
            tick={{ fill: 'var(--color-ink-dim)', fontSize: 12, fontFamily: 'var(--font-mono)' }}
            tickFormatter={(v: number) => fmtNum(v, 0)}
            width={48}
          />
          {band('WARNING', 1) != null && (
            <ReferenceArea y1={band('WARNING', -1)!} y2={band('WARNING', 1)!} fill="var(--color-sev-watch)" fillOpacity={0.07} ifOverflow="extendDomain" />
          )}
          {band('WATCH', 1) != null && (
            <ReferenceArea y1={band('WATCH', -1)!} y2={band('WATCH', 1)!} fill="var(--color-signal)" fillOpacity={0.09} ifOverflow="extendDomain" />
          )}
          {med != null && (
            <ReferenceLine
              y={med}
              stroke="var(--color-signal)"
              strokeDasharray="4 4"
              label={{ value: `median ${fmtNum(med)}`, position: 'insideTopLeft', fill: 'var(--color-signal)', fontSize: 12 }}
            />
          )}
          <Line dataKey="v" stroke="var(--color-seam-strong)" strokeWidth={1} dot={false} isAnimationActive={false} />
          <Scatter
            dataKey="v"
            isAnimationActive={false}
            shape={(p: { cx?: number; cy?: number; payload?: { sev: string } }) => (
              <circle
                cx={p.cx}
                cy={p.cy}
                r={p.payload?.sev === 'NORMAL' || p.payload?.sev === 'UNASSESSED' ? 2.2 : 3.6}
                fill={p.payload?.sev === 'UNASSESSED' ? 'var(--color-ink-faint)' : severityColor(p.payload?.sev)}
              />
            )}
          />
          <Tooltip
            cursor={{ stroke: 'var(--color-seam-strong)' }}
            content={({ active, payload }) => {
              const d = active && payload?.[0]?.payload
              if (!d) return null
              return (
                <div className="rounded-md border border-seam-strong bg-hull px-3 py-2 text-sm shadow-lg">
                  <div className="text-ink-dim">{fmtDateTime(new Date(d.t).toISOString())}</div>
                  <div className="num text-ink">
                    {fmtNum(d.v)} {unitLabel(d.unit)} <span className="text-ink-dim">{fmtZ(d.z)}</span>
                  </div>
                  <div style={{ color: d.sev === 'UNASSESSED' ? undefined : severityColor(d.sev) }}>
                    {d.sev === 'UNASSESSED' ? 'Not assessed (no baseline yet)' : d.sev.toLowerCase()}
                    {d.sim ? ', simulated' : ''}
                  </div>
                </div>
              )
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
