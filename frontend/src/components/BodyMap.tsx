import { useQuery } from '@tanstack/react-query'
import { Component, lazy, Suspense, useCallback, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  guidanceApi,
  type BodyMapData,
  type BodyStatus,
  type BodySystem,
  type LevelGuidance,
  type SystemReading,
  type SystemSymptom,
} from '../api/guidance'
import { contextLabel, fmtAgo, fmtDateTime, fmtNum, severityColor, unitLabel } from '../lib/format'
import { GuidancePanel, SymptomGuidance } from './Guidance'
import { ErrorNote, Loading, SeverityTag, SimulatedMark } from './ui'
import { CabinStage } from './CabinStage'
import { webglAvailable } from './body3dSupport'

// three.js is only downloaded when the 3D body map is shown.
const Body3D = lazy(() => import('./Body3D'))
const VIEW_KEY = 'am.bodymap.view'

/* Body status colours reuse the severity tokens: red = critical, yellow = warning. */
const STATUS_COLOR: Record<BodyStatus, string> = {
  red: 'var(--color-sev-critical)',
  yellow: 'var(--color-sev-warning)',
  watch: 'var(--color-sev-watch)',
  green: 'var(--color-sev-normal)',
  no_data: 'var(--color-ink-faint)',
}
const STATUS_RANK: Record<BodyStatus, number> = { red: 4, yellow: 3, watch: 2, green: 1, no_data: 0 }
// Used until the DEMO matrix wording has loaded; afterwards the matrix's plain meaning is shown.
const STATUS_FALLBACK: Record<BodyStatus, string> = {
  red: 'Critical level',
  yellow: 'Warning level',
  watch: 'Watch level',
  green: 'Nothing open',
  no_data: 'Nothing recorded',
}
const TOPIC_LABEL: Record<string, string> = { sans: 'spaceflight associated neuro-ocular syndrome (SANS)' }

type Levels = Map<string, LevelGuidance>

function statusText(status: BodyStatus, level: string | null, levels: Levels): string {
  return (level && levels.get(level)?.plain_meaning) || STATUS_FALLBACK[status]
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/** What is open, or how fresh the records are. Null when nothing is recorded (the status says so). */
function summary(s: BodySystem): string | null {
  const parts: string[] = []
  if (s.open_alerts.length) parts.push(plural(s.open_alerts.length, 'open alert'))
  if (s.active_symptoms.length) parts.push(plural(s.active_symptoms.length, 'active symptom'))
  if (parts.length) return parts.join(', ')
  return s.last_data_at ? `Last record ${fmtAgo(s.last_data_at)}` : null
}

/** Continue a sentence with `text` (first letter lowered), or nothing when there is no text. */
function lowerFirst(text: string | null, lead = ''): string {
  return text ? `${lead}${text.charAt(0).toLowerCase()}${text.slice(1)}` : ''
}

function describe(s: BodySystem, levels: Levels): string {
  const more = summary(s)
  return `${s.label}: ${statusText(s.status, s.level, levels).toLowerCase()}.${more ? ` ${more}.` : ''}`
}

/* ------------------------------------------------------------------ figure geometry
 * An original, deliberately simple front-facing silhouette (viewBox 200 x 410). The
 * figure's left side is drawn on the viewer's right, so the heart sits right of centre.
 * Paths use absolute "x,y" pairs so the right-hand limbs can be mirrored from the left.
 */
const W = 200
const mirror = (d: string) => d.replace(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (_m, x: string, y: string) => `${W - Number(x)},${y}`)

const HEAD = { cx: 100, cy: 42, rx: 21, ry: 26 }
const NECK = 'M92,62 L108,62 L110,88 L90,88 Z'
const TORSO =
  'M90,84 C80,86 64,88 58,96 C54,101 54,110 57,120 L64,168 C66,186 66,200 64,214 C62,226 64,236 70,242 ' +
  'L130,242 C136,236 138,226 136,214 C134,200 134,186 136,168 L143,120 C146,110 146,101 142,96 C136,88 120,86 110,84 Z'
const ARM_L =
  'M60,94 C48,98 44,112 44,128 L38,196 L33,248 C32,258 34,266 39,268 C44,269 47,262 46,252 L50,200 L56,140 ' +
  'C57,128 60,118 64,112 Z'
const LEG_L =
  'M66,228 C63,258 65,292 69,318 C71,340 71,362 73,380 C73,388 68,393 69,397 L95,397 C97,393 95,387 93,382 ' +
  'C92,362 91,340 92,318 C93,292 97,266 98,248 L98,230 Z'
const LIMBS = [ARM_L, mirror(ARM_L), LEG_L, mirror(LEG_L)]
const BODY = [NECK, TORSO, ...LIMBS]
const BRAIN =
  'M85,39 C82,31 87,24 95,24 C98,22 102,22 105,24 C113,24 118,31 115,39 C113,43 109,44 106,42 C103,44 97,44 94,42 ' +
  'C91,44 87,43 85,39 Z'
const BRAIN_FOLDS = 'M95,27 C92,31 97,34 94,39 M105,27 C108,31 103,34 106,39 M100,24 L100,41'
const EYE_L = 'M86,53 C88,50 96,50 98,53 C96,56 88,56 86,53 Z'
const LUNG_L = 'M96,102 C88,100 78,104 74,116 C70,130 69,148 71,162 C72,168 80,170 88,166 C92,164 96,162 96,156 Z'
const HEART =
  'M106,150 C98,144 94,138 94,132 C94,127 98,124 102,125 C104,125.5 105.5,127 106,128.5 C106.5,127 108,125.5 110,125 ' +
  'C114,124 118,127 118,132 C118,138 114,144 106,150 Z'
const ABDOMEN = 'M78,178 C84,172 116,172 122,178 C128,190 128,210 120,222 C112,230 88,230 80,222 C72,210 72,190 78,178 Z'
const GUT = 'M86,196 C92,190 100,202 106,196 C112,190 116,202 112,208 C106,214 96,206 90,212 C86,216 90,222 96,220'
const VERTEBRAE = Array.from({ length: 19 }, (_, i) => 70 + i * 9)
const METABOLIC = { cx: 170, cy: 324, r: 16 }
const DROP = 'M170,313 C166,319 163,323 163,327.5 C163,331.6 166.2,334.5 170,334.5 C173.8,334.5 177,331.6 177,327.5 C177,323 174,319 170,313 Z'

interface ShapeStyle {
  fill: string
  fillOpacity: number
  stroke: string
  strokeWidth: number
  strokeDasharray?: string
  className: string
}

function shapeStyle(status: BodyStatus, selected: boolean): ShapeStyle {
  const color = STATUS_COLOR[status]
  const empty = status === 'no_data'
  return {
    fill: color,
    fillOpacity: selected ? 0.55 : empty ? 0.06 : 0.3,
    stroke: selected ? 'var(--color-ink)' : color,
    strokeWidth: selected ? 2 : 1.25,
    strokeDasharray: empty ? '3 2' : undefined,
    className: 'shape transition-[fill-opacity]',
  }
}

/** An organ drawn over an opaque underlay, so the spine never shows through it. */
function Solid({ d, st }: { d: string; st: ShapeStyle }) {
  return (
    <>
      <path d={d} fill="var(--color-deck)" />
      <path d={d} {...st} />
    </>
  )
}

// "shape" and "skin-edge" are plain marker classes for these selectors (not Tailwind utilities).
// Focus is shown on the region's own strokes; the box outline is switched off inline (see NO_BOX).
const REGION_CLS =
  'cursor-pointer [&:hover_.shape]:[fill-opacity:0.5] [&:hover_.skin-edge]:[stroke-width:6px] ' +
  '[&:focus-visible_.shape]:[stroke:var(--color-signal)] [&:focus-visible_.shape]:[stroke-width:2.5px] ' +
  '[&:focus-visible_.skin-edge]:[stroke:var(--color-signal)]'
// The global :focus-visible outline (index.css) is unlayered, so it outranks Tailwind's
// outline-none; an inline style is needed to stop it drawing a box around each region.
const NO_BOX = { outline: 'none' }

function Figure({
  systems,
  selected,
  onSelect,
  levels,
}: {
  systems: BodySystem[]
  selected: string
  onSelect: (code: string) => void
  levels: Levels
}) {
  const refs = useRef(new Map<string, SVGGElement>())
  const [hovered, setHovered] = useState<string | null>(null)
  const order = systems.map((s) => s.code)
  const by = new Map(systems.map((s) => [s.code, s]))

  // Roving focus: one region is in the tab order; arrow keys move through the systems
  // head to toe, independent of the drawing order the layered shapes need.
  const onKey = (code: string) => (e: KeyboardEvent<SVGGElement>) => {
    const i = order.indexOf(code)
    const next =
      e.key === 'ArrowDown' || e.key === 'ArrowRight' ? order[(i + 1) % order.length]
        : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? order[(i - 1 + order.length) % order.length]
          : e.key === 'Home' ? order[0]
            : e.key === 'End' ? order[order.length - 1]
              : null
    if (next) {
      e.preventDefault()
      onSelect(next)
      refs.current.get(next)?.focus()
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(code)
    }
  }

  const region = (code: string, draw: (st: ShapeStyle, isSelected: boolean) => ReactNode) => {
    const s = by.get(code)
    if (!s) return null
    const isSelected = code === selected
    return (
      <g
        key={code}
        ref={(el) => {
          if (el) refs.current.set(code, el)
          else refs.current.delete(code)
        }}
        role="radio"
        aria-checked={isSelected}
        aria-label={describe(s, levels)}
        tabIndex={isSelected ? 0 : -1}
        className={REGION_CLS}
        style={NO_BOX}
        onClick={() => onSelect(code)}
        onKeyDown={onKey(code)}
        onMouseEnter={() => setHovered(s.label)}
        onMouseLeave={() => setHovered(null)}
      >
        {draw(shapeStyle(s.status, isSelected), isSelected)}
      </g>
    )
  }

  const figure = (
    <svg viewBox="0 0 200 410" role="radiogroup" aria-label="Body map. Use the arrow keys to move between body systems."
      className="h-auto w-full select-none">
      {/* Immune system and skin: the body outline. A wide transparent stroke makes it easy to hit. */}
      {region('immune_skin', (st, isSelected) => {
        const outline = {
          fill: 'none',
          stroke: st.stroke,
          strokeWidth: isSelected ? 7 : 4.5,
          strokeDasharray: st.strokeDasharray,
          strokeLinejoin: 'round' as const,
          className: 'skin-edge transition-[stroke-width]',
        }
        return (
          <>
            <ellipse {...HEAD} {...outline} />
            {BODY.map((d, i) => <path key={i} d={d} {...outline} />)}
            <g fill="none" stroke="transparent" strokeWidth={14} pointerEvents="stroke">
              <ellipse {...HEAD} />
              {BODY.map((d, i) => <path key={i} d={d} />)}
            </g>
          </>
        )
      })}
      {/* Opaque body surface: hides the inner half of the outline stroke and the seams between parts. */}
      <g fill="var(--color-deck)" pointerEvents="none" aria-hidden>
        <ellipse {...HEAD} />
        {BODY.map((d, i) => <path key={i} d={d} />)}
      </g>
      {region('musculoskeletal', (st) => (
        <>
          {VERTEBRAE.map((y) => <rect key={y} x={97} y={y} width={6} height={6.5} rx={1.5} {...st} />)}
          {LIMBS.map((d, i) => <path key={i} d={d} {...st} />)}
        </>
      ))}
      {region('neuro_behavioral', (st) => (
        <>
          <Solid d={BRAIN} st={st} />
          <path d={BRAIN_FOLDS} fill="none" stroke={st.stroke} strokeOpacity={0.6} strokeWidth={1} strokeLinecap="round" />
        </>
      ))}
      {region('eyes', (st) => (
        <>
          <rect x={83} y={46} width={34} height={14} fill="transparent" />
          <Solid d={EYE_L} st={st} />
          <Solid d={mirror(EYE_L)} st={st} />
          <circle cx={92} cy={53} r={1.4} fill={st.stroke} />
          <circle cx={108} cy={53} r={1.4} fill={st.stroke} />
        </>
      ))}
      {region('respiratory', (st) => (
        <>
          <Solid d={LUNG_L} st={st} />
          <Solid d={mirror(LUNG_L)} st={st} />
        </>
      ))}
      {region('cardiovascular', (st) => (
        <g transform="rotate(-20 106 137)">
          <Solid d={HEART} st={st} />
        </g>
      ))}
      {region('gastrointestinal', (st) => (
        <>
          <Solid d={ABDOMEN} st={st} />
          <path d={GUT} fill="none" stroke={st.stroke} strokeOpacity={0.6} strokeWidth={1} strokeLinecap="round" />
        </>
      ))}
      {/* Metabolism has no single organ, so it is shown as an indicator beside the figure. */}
      {region('metabolic', (st) => (
        <>
          <circle {...METABOLIC} {...st} />
          <path d={DROP} fill={st.stroke} fillOpacity={0.85} />
          <text x={METABOLIC.cx} y={METABOLIC.cy + METABOLIC.r + 12} textAnchor="middle" fontSize={10}
            fill="var(--color-ink-dim)">
            Metabolic
          </text>
        </>
      ))}
    </svg>
  )

  return (
    <div className="flex w-full flex-col items-center gap-1">
      <CabinStage>{figure}</CabinStage>
      {/* Names the region under the pointer; keyboard and screen-reader users get it from aria-label. */}
      <p aria-hidden className="h-4 text-center text-xs text-ink-dim">{hovered ?? ''}</p>
    </div>
  )
}

function StatusMark({ status }: { status: BodyStatus }) {
  // Hollow ring for "no data", so the state is not carried by colour alone.
  const empty = status === 'no_data'
  return (
    <span
      aria-hidden
      className="mt-1 inline-block size-2.5 shrink-0 rounded-full"
      style={empty ? { boxShadow: `inset 0 0 0 1.5px ${STATUS_COLOR[status]}` } : { background: STATUS_COLOR[status] }}
    />
  )
}

function SystemList({
  systems,
  selected,
  onSelect,
  levels,
}: {
  systems: BodySystem[]
  selected: string
  onSelect: (code: string) => void
  levels: Levels
}) {
  return (
    <ul aria-label="Body systems" className="flex flex-col border-t border-seam">
      {systems.map((s) => (
        <li key={s.code}>
          <button
            type="button"
            onClick={() => onSelect(s.code)}
            aria-pressed={s.code === selected}
            className={`flex w-full gap-2.5 border-b border-seam/60 px-2.5 py-2 text-left transition-colors ${
              s.code === selected ? 'bg-deck shadow-[inset_2px_0_0_var(--color-signal)]' : 'hover:bg-deck/50'
            }`}
          >
            <StatusMark status={s.status} />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-ink">{s.label}</span>
              <span className="block text-xs text-ink-dim">
                <span style={{ color: s.status === 'no_data' ? undefined : STATUS_COLOR[s.status] }}>
                  {statusText(s.status, s.level, levels)}
                </span>
                {lowerFirst(summary(s), ', ')}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function Legend({ bm, levels }: { bm: BodyMapData; levels: Levels }) {
  const levelFor: Partial<Record<BodyStatus, string>> = { red: 'CRITICAL', yellow: 'WARNING', watch: 'WATCH' }
  return (
    <div className="flex w-full flex-col gap-2 text-xs">
      <ul aria-label="Colour key" className="grid grid-cols-2 gap-x-3 gap-y-1 text-ink-dim">
        {bm.rules.map((r) => (
          <li key={r.status} className="flex gap-1.5">
            <StatusMark status={r.status} />
            {statusText(r.status, levelFor[r.status] ?? null, levels)}
          </li>
        ))}
      </ul>
      <details className="text-ink-dim">
        <summary className="cursor-pointer hover:text-ink">How colours are chosen</summary>
        <ul className="mt-1.5 space-y-1">
          {bm.rules.map((r) => (
            <li key={r.status}>
              <span className="font-medium text-ink">{statusText(r.status, levelFor[r.status] ?? null, levels)}:</span>{' '}
              {r.meaning}
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-ink-faint">{bm.rules_note}</p>
      </details>
      <span className="text-sev-watch">{bm.label}</span>
    </div>
  )
}

function readingAssessment(m: SystemReading): ReactNode {
  if (m.source === 'self_report') return <span className="text-xs text-ink-dim">Self-rating, not assessed</span>
  if (m.severity) return <SeverityTag severity={m.severity} />
  return (
    <span className="text-xs text-ink-faint">
      {m.assessment_status === 'no_baseline' ? 'No baseline yet' : 'Not assessed'}
    </span>
  )
}

function SymptomItem({ s }: { s: SystemSymptom }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-seam/60 py-2 last:border-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="mt-0.5 size-2 shrink-0 rounded-full" style={{ background: severityColor(s.level) }} aria-hidden />
        <span className="font-medium text-ink">{s.label}</span>
        <span className="text-sm text-ink-dim">rated {s.severity}</span>
        {s.report_promptly && <span className="text-xs text-sev-watch">DEMO report-promptly list</span>}
        <span className="text-xs text-ink-faint" title={fmtDateTime(s.reported_at)}>reported {fmtAgo(s.reported_at)}</span>
        {s.is_simulated && <SimulatedMark />}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="ml-auto text-sm text-signal hover:underline"
        >
          {open ? 'Hide guidance' : 'Is this serious?'}
        </button>
      </div>
      {open && (
        <div className="mt-3">
          <SymptomGuidance reportId={s.id} heading="h5" />
        </div>
      )}
    </li>
  )
}

function SubHeading({ children }: { children: ReactNode }) {
  return <h4 className="mb-1.5 text-sm font-medium text-ink-dim">{children}</h4>
}

function SystemDetail({ s, bm, levels }: { s: BodySystem; bm: BodyMapData; levels: Levels }) {
  const entry = s.level ? levels.get(s.level) : undefined
  const simulated = [...s.metrics, ...s.active_symptoms, ...s.open_alerts].some((r) => r.is_simulated)
  return (
    <section aria-labelledby={`bodymap-${s.code}`} className="flex flex-col gap-6 border-t border-seam pt-5">
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 id={`bodymap-${s.code}`} className="text-lg font-semibold">{s.label}</h3>
          <span className="inline-flex items-center gap-1.5 text-sm" style={{ color: s.status === 'no_data' ? undefined : STATUS_COLOR[s.status] }}>
            <StatusMark status={s.status} />
            {statusText(s.status, s.level, levels)}
          </span>
          {simulated && <SimulatedMark title="Some records shown for this system were generated by the AM simulator." />}
        </div>
        <p className="text-xs text-ink-dim">
          {s.coverage}
          {s.last_data_at && <> Last record {fmtAgo(s.last_data_at)}.</>}
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <SubHeading>Why this colour</SubHeading>
          <ul className="space-y-1.5 text-sm">
            {s.reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full"
                  style={{ background: r.level ? severityColor(r.level) : 'var(--color-seam-strong)' }} />
                {r.ref_type === 'alert' && r.ref_id != null ? (
                  <Link to={`/alerts?id=${r.ref_id}`} className="text-ink hover:text-signal">{r.text}</Link>
                ) : (
                  <span className="text-ink">{r.text}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
        {s.level && entry && <GuidancePanel level={s.level} entry={entry} label={bm.label} heading="h4" />}
      </div>

      {s.metrics.length > 0 && (
        <div>
          <SubHeading>Latest readings</SubHeading>
          <table className="w-full text-sm">
            <thead className="text-left text-ink-dim">
              <tr>
                <th className="pb-1 font-normal">Indicator</th>
                <th className="pb-1 text-right font-normal">Latest</th>
                <th className="pb-1 pl-4 font-normal">Assessment</th>
                <th className="hidden pb-1 text-right font-normal sm:table-cell">When</th>
              </tr>
            </thead>
            <tbody>
              {s.metrics.map((m) => (
                <tr key={`${m.code}-${m.context}`} className="border-t border-seam/60">
                  <td className="py-1.5 pr-3">
                    {m.source === 'measurement' ? (
                      <Link to={`/health?metric=${m.code}&context=${m.context}`} className="text-ink hover:text-signal">
                        {m.display_name}
                      </Link>
                    ) : (
                      <span className="text-ink">{m.display_name}</span>
                    )}
                    <span className="block text-xs text-ink-dim">
                      {m.source === 'self_report' ? 'Daily check-in' : contextLabel(m.context)}
                    </span>
                  </td>
                  <td className="num py-1.5 text-right">
                    {fmtNum(m.value)}
                    <span className="ml-1 text-xs text-ink-dim">{unitLabel(m.unit)}</span>
                  </td>
                  <td className="py-1.5 pl-4">{readingAssessment(m)}</td>
                  <td className="hidden py-1.5 text-right text-xs text-ink-faint sm:table-cell" title={fmtDateTime(m.recorded_at)}>
                    {fmtAgo(m.recorded_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {s.active_symptoms.length > 0 && (
        <div>
          <SubHeading>Active symptoms</SubHeading>
          <ul className="text-sm">{s.active_symptoms.map((x) => <SymptomItem key={x.id} s={x} />)}</ul>
        </div>
      )}

      <div>
        <SubHeading>Related reading</SubHeading>
        {s.documents.length > 0 ? (
          <ul className="space-y-1.5 text-sm">
            {s.documents.map((d) => (
              <li key={d.slug}>
                <a href={d.url} target="_blank" rel="noreferrer" className="text-ink hover:text-signal">{d.title}</a>
                <span className="block text-xs text-ink-dim">
                  {d.organization}. Topic: {d.matched_topics.map((t) => TOPIC_LABEL[t] ?? t.replaceAll('_', ' ')).join(', ')}
                </span>
              </li>
            ))}
          </ul>
        ) : bm.knowledge_indexed ? (
          <p className="text-sm text-ink-dim">No curated source covers this system yet.</p>
        ) : (
          <p className="text-sm text-ink-dim">
            Index the curated sources in{' '}
            <Link to="/science?tab=library" className="text-signal underline underline-offset-2">Science, literature</Link>{' '}
            to see related reading.
          </p>
        )}
        <p className="mt-1.5 text-xs text-ink-faint">Matched by topic. General background, not evidence about this astronaut.</p>
      </div>
    </section>
  )
}

/** If the 3D view cannot load (a missing chunk) or throws while drawing, show the flat map instead of a blank app. */
class Body3DBoundary extends Component<{ fallback: ReactNode; onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch() {
    this.props.onError()
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

function readView(): '3d' | 'flat' {
  try {
    return localStorage.getItem(VIEW_KEY) === 'flat' ? 'flat' : '3d'
  } catch {
    return '3d'
  }
}

/** The figure: 3D in the crew quarters when the browser can draw it, else (or by choice) the flat map. */
function BodyView({ systems, selected, onSelect, levels }: {
  systems: BodySystem[]
  selected: BodySystem
  onSelect: (code: string) => void
  levels: Levels
}) {
  const [can3d, setCan3d] = useState(webglAvailable)
  const [view, setView] = useState<'3d' | 'flat'>(readView)
  const pick = (v: '3d' | 'flat') => {
    setView(v)
    try { localStorage.setItem(VIEW_KEY, v) } catch { /* private mode */ }
  }
  const failed = useCallback(() => setCan3d(false), [])
  const flat = <Figure systems={systems} selected={selected.code} onSelect={onSelect} levels={levels} />
  return (
    <>
      {can3d && (
        <div role="group" aria-label="Body map view" className="inline-flex rounded-md border border-seam-strong p-0.5 text-xs">
          {(['3d', 'flat'] as const).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => pick(v)}
              className={`rounded px-2.5 py-1 ${view === v ? 'bg-deck text-ink' : 'text-ink-dim hover:text-ink'}`}>
              {v === '3d' ? '3D view' : 'Flat view'}
            </button>
          ))}
        </div>
      )}
      {can3d && view === '3d' ? (
        <Body3DBoundary fallback={flat} onError={failed}>
          <Suspense fallback={flat}>
            <Body3D systems={systems} selected={selected.code} onSelect={onSelect}
              selectedText={describe(selected, levels)} onFail={failed} />
          </Suspense>
        </Body3DBoundary>
      ) : flat}
    </>
  )
}

function mostSevere(systems: BodySystem[]): BodySystem {
  return systems.reduce((best, s) => (STATUS_RANK[s.status] > STATUS_RANK[best.status] ? s : best), systems[0])
}

/**
 * Clickable body map: one region per body system, coloured by its status, with a text
 * list that offers the same choices and states every status in words.
 */
export function BodyMap({ astronautId }: { astronautId: number }) {
  const q = useQuery({
    queryKey: ['bodymap', astronautId],
    queryFn: () => guidanceApi.bodyMap(astronautId),
    refetchInterval: 15_000,
  })
  const levelsQ = useQuery({ queryKey: ['guidance', 'levels'], queryFn: guidanceApi.levels, staleTime: Infinity })
  const [picked, setPicked] = useState<string | null>(null)

  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading label="Loading body map" />
  const bm = q.data
  if (!bm.systems.length) return null
  const levels: Levels = new Map((levelsQ.data?.levels ?? []).map((l) => [l.level, l]))
  const selected = bm.systems.find((s) => s.code === picked) ?? mostSevere(bm.systems)

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 sm:grid-cols-[320px_minmax(0,1fr)]">
        <div className="flex flex-col items-center gap-3">
          <BodyView systems={bm.systems} selected={selected} onSelect={setPicked} levels={levels} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <SystemList systems={bm.systems} selected={selected.code} onSelect={setPicked} levels={levels} />
          <Legend bm={bm} levels={levels} />
        </div>
      </div>
      <SystemDetail s={selected} bm={bm} levels={levels} />
    </div>
  )
}
