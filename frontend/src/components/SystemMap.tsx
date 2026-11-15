// AMC system map: three live diagrams that replace the wall of status text on the System page.
// Adapted from the 21st.dev "ink-orbit-features" bento (kedhareswer): the flow diagram, the
// integrations hub and the scrubbable chart, redrawn in the AMC palette and fed only with stored
// records. Nothing here is decorative data:
//   1. Crew -> AMC -> reports: the real crew (colour = their current flag level) and the real
//      number of sealed AMC Core reports; a new sheet slides in only when a real report is written.
//   2. Local services: database, chat model, embedding model and the AMC Core seal, from
//      /system/status and /am/core/status; the hub cycles through them, hover to hold one.
//   3. Written to AMC today by hour, from /doctor/analytics. The original drew a "forecast";
//      AMC does not predict, so the chart shows recorded values only.
// No dependencies and no external assets: it works offline.

import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { api, request } from '../api/client'
import { doctorApi, type CrewMember } from '../api/doctor'

interface CoreStatus { open: boolean; reports: number; last_close_date: string | null }
interface Analytics { activity: { written: number[][] } }

const LEVEL_COLOR: Record<string, string> = {
  red: 'var(--color-sev-critical)',
  yellow: 'var(--color-sev-watch)',
  none: 'var(--color-sev-normal)',
}

/* ---------------------------------------------------------------- helpers */

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

function useReducedMotion() {
  const [r, setR] = useState(false)
  useEffect(() => {
    const m = window.matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setR(m.matches)
    on()
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return r
}

function useInView(threshold = 0.2) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || inView) return
    if (typeof IntersectionObserver !== 'function') { setInView(true); return }
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { setInView(true); io.disconnect() }
    }, { threshold })
    io.observe(el)
    return () => io.disconnect()
  }, [inView, threshold])
  return [ref, inView] as const
}

/** Smooth line and closed area through the values, inside a w x h box. */
function chartPaths(values: number[], w: number, h: number, pad: number) {
  const n = values.length
  const lo = Math.min(0, ...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const pts = values.map((v, i) => [
    pad + (n < 2 ? 0 : (i / (n - 1)) * (w - pad * 2)),
    h - pad - ((v - lo) / span) * (h - pad * 2),
  ] as [number, number])
  let line = ''
  pts.forEach(([x, y], i) => {
    if (i === 0) { line = `M${x.toFixed(1)},${y.toFixed(1)}`; return }
    const [px, py] = pts[i - 1]
    const cx = (px + x) / 2
    line += ` C${cx.toFixed(1)},${py.toFixed(1)} ${cx.toFixed(1)},${y.toFixed(1)} ${x.toFixed(1)},${y.toFixed(1)}`
  })
  const area = n ? `${line} L${pts[n - 1][0].toFixed(1)},${h - pad} L${pts[0][0].toFixed(1)},${h - pad} Z` : ''
  return { line, area, points: pts }
}

const nearest = (xs: number[], x: number) => xs.reduce((b, v, i) => (Math.abs(v - x) < Math.abs(xs[b] - x) ? i : b), 0)

/* ----------------------------------------------------------------- pieces */

function Brackets() {
  return (
    <>
      <span className="sm-c sm-c-tl" aria-hidden />
      <span className="sm-c sm-c-tr" aria-hidden />
      <span className="sm-c sm-c-bl" aria-hidden />
      <span className="sm-c sm-c-br" aria-hidden />
    </>
  )
}

function Frame({ wide, children, title, text }: { wide?: boolean; children: ReactNode; title: string; text: ReactNode }) {
  return (
    <div className={`sm-frame ${wide ? 'sm-wide' : ''}`}>
      <Brackets />
      <div className="sm-card">
        {children}
        <div className="mt-auto">
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-dim">{text}</p>
        </div>
      </div>
    </div>
  )
}

/** The AMC glyph: a ring around a core, as in the navigation. */
function Mark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 12v2" />
    </svg>
  )
}

function ServiceGlyph({ kind }: { kind: string }) {
  return (
    <g fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {kind === 'database' && (
        <>
          <ellipse cx="0" cy="-4" rx="6" ry="2.4" />
          <path d="M-6 -4v8c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4v-8M-6 0c0 1.3 2.7 2.4 6 2.4S6 1.3 6 0" />
        </>
      )}
      {kind === 'chat' && <path d="M-6 -5h12v8H-1l-4 3v-3h-1z" />}
      {kind === 'embed' && (
        <>
          <circle cx="-4" cy="-3" r="1.4" /><circle cx="3" cy="-4" r="1.4" /><circle cx="0" cy="3" r="1.4" /><circle cx="5" cy="3" r="1.4" />
          <path d="M-4 -3 0 3 3 -4 5 3" />
        </>
      )}
      {kind === 'seal' && (
        <>
          <rect x="-5" y="-1" width="10" height="7" rx="1.2" />
          <path d="M-3 -1v-2.5a3 3 0 016 0V-1" />
        </>
      )}
    </g>
  )
}

/* ------------------------------------------------------ 1: crew -> AMC -> reports */

const SEATS = [
  { x: 58, y: 38 }, { x: 104, y: 22 }, { x: 150, y: 40 }, { x: 46, y: 92 }, { x: 146, y: 114 },
]
const L1 = 'M117,67 C196,67 210,100 262,100'
const L2 = 'M117,73 C186,73 206,120 262,120'
const R1 = 'M378,100 C412,100 418,86 450,86'
const R2 = 'M378,120 C412,120 418,134 450,134'

function FlowCard({ crew, core, uid, reduced }: { crew: CrewMember[]; core?: CoreStatus; uid: string; reduced: boolean }) {
  const [side, setSide] = useState<'' | 'left' | 'right'>('')
  const seated = crew.slice(0, SEATS.length)
  const extra = crew.length - seated.length
  const reports = core?.reports ?? 0
  const writing = crew.filter((c) => c.level !== 'none').length
  return (
    <Frame wide title="Crew → AMC → reports"
      text={<>Everything the crew writes reaches AMC; at End Day AMC seals a report per astronaut and one for the crew. {core ? <>AMC Core holds <b className="num text-ink">{reports}</b> report{reports === 1 ? '' : 's'}{core.last_close_date ? <>, the last for <span className="num">{core.last_close_date}</span></> : ''}.</> : 'Loading AMC Core…'}</>}>
      <svg className="sm-diagram" viewBox="0 0 640 178" role="img"
        aria-label={`${crew.length} crew members feed AMC; ${writing} have something flagged; AMC Core holds ${reports} reports`}>
        <defs>
          <radialGradient id={`${uid}glow`} cx="320" cy="110" r="190" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="var(--color-signal)" stopOpacity=".10" />
            <stop offset="1" stopColor="var(--color-signal)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width="640" height="178" fill={`url(#${uid}glow)`} />

        <g onMouseEnter={() => setSide('left')} onMouseLeave={() => setSide('')}>
          <rect x="20" y="4" width="170" height="134" fill="transparent" />
          {seated.map((_, i) => (
            <line key={i} x1={SEATS[i].x} y1={SEATS[i].y} x2="104" y2="70" stroke="var(--color-seam-strong)" strokeDasharray="2 3" />
          ))}
          <g transform="translate(91,57)">
            <rect width="26" height="26" rx="5" className="sm-node" />
            <g transform="translate(5,5)" style={{ color: 'var(--color-ink)' }}><Mark /></g>
          </g>
          {seated.map((m, i) => {
            const s = SEATS[i]
            const label = `${m.code} · ${m.display_name}${m.mission_day != null ? ` · day ${m.mission_day}` : ''}`
            return (
              <g key={m.astronaut_id} className="sm-av" tabIndex={0} role="img" aria-label={`${label}, flag ${m.level}`}>
                <circle cx={s.x} cy={s.y} r="11" fill="var(--color-deck)" stroke="var(--color-seam-strong)" />
                <text x={s.x} y={s.y + 3} textAnchor="middle" fontSize="7.5" fill="var(--color-ink)" fontFamily="var(--font-sans)">
                  {m.code.replace(/\D/g, '').slice(-2) || m.code.slice(0, 2)}
                </text>
                <circle cx={s.x + 8} cy={s.y + 8} r="2.8" fill={LEVEL_COLOR[m.level] ?? 'var(--color-ink-faint)'} stroke="var(--color-hull)"
                  className={m.level === 'red' && !reduced ? 'sm-pulse' : undefined} />
                <g className="sm-tip" transform={`translate(${s.x},${s.y - 18})`}>
                  <rect x={-label.length * 2.45 - 6} y="-9" width={label.length * 4.9 + 12} height="14" rx="2" fill="var(--color-ink)" />
                  <text y="1" textAnchor="middle" fontSize="7.5" fill="var(--color-void)" fontFamily="var(--font-sans)">{label}</text>
                </g>
              </g>
            )
          })}
          {extra > 0 && <text x="104" y="136" textAnchor="middle" fontSize="8" fill="var(--color-ink-faint)" fontFamily="var(--font-sans)">+{extra} more</text>}
        </g>

        {[L1, L2].map((d, i) => <path key={d} d={d} className={`sm-flow${side === 'left' ? ' sm-flow-on' : ''}${i ? ' sm-dash' : ''}`} />)}
        {[R1, R2].map((d, i) => <path key={d} d={d} className={`sm-flow${side === 'right' ? ' sm-flow-on' : ''}${i ? ' sm-dash' : ''}`} />)}
        {!reduced && [L1, L2, R1, R2].map((d, i) => (
          <circle key={i} r="2.2" className="sm-pkt">
            <animateMotion dur={`${2.2 + (i % 2) * 0.6}s`} begin={`${i * 0.35}s`} repeatCount="indefinite" path={d} />
          </circle>
        ))}

        <g transform="translate(262,88)">
          <rect x="-4" y="-4" width="124" height="52" rx="8" fill="none" stroke="var(--color-signal-dim)" className={reduced ? undefined : 'sm-pulse'} />
          <rect width="116" height="44" rx="6" className="sm-node" />
          <g transform="translate(13,14)" style={{ color: 'var(--color-signal)' }}><Mark /></g>
          <text x="36" y="20" fontSize="8.5" letterSpacing="1.4" fontWeight="600" fill="var(--color-ink)" fontFamily="var(--font-sans)">AMC</text>
          <text x="36" y="32" fontSize="7" fill="var(--color-ink-faint)" fontFamily="var(--font-sans)">{core ? (core.open ? 'Open' : 'Sealed') : '…'}</text>
          <path d="M100 18l4 4-4 4" fill="none" stroke="var(--color-ink-dim)" strokeWidth="1.3" />
        </g>

        <g onMouseEnter={() => setSide('right')} onMouseLeave={() => setSide('')}>
          {[2, 1, 0].map((k) => (
            <g key={`${reports}-${k}`} transform={`translate(${450 + k * 12},${40 - k * 10})`} opacity={1 - k * 0.25}>
              <g className={k === 0 && !reduced ? 'sm-sheet-new' : undefined}>
                <rect width="138" height="102" rx="3" fill="var(--color-deck)" stroke="var(--color-seam-strong)" />
                {k === 0 && (
                  <>
                    <text x="10" y="16" fontSize="7" fill="var(--color-ink-faint)" fontFamily="var(--font-sans)">
                      {reports ? `Report ${reports}${core?.last_close_date ? ` · ${core.last_close_date}` : ''}` : 'No reports yet'}
                    </text>
                    <rect x="10" y="24" width="64" height="5" rx="1" fill="var(--color-ink)" opacity=".7" />
                    <rect x="10" y="33" width="92" height="3" rx="1" fill="var(--color-seam-strong)" />
                    <rect x="10" y="39" width="80" height="3" rx="1" fill="var(--color-seam-strong)" />
                    <path d="M10 64h70M10 72h60M10 80h66M92 64h34M92 72h28M92 80h32" stroke="var(--color-seam-strong)" strokeWidth="2.5" />
                  </>
                )}
              </g>
            </g>
          ))}
        </g>
      </svg>
    </Frame>
  )
}

/* ------------------------------------------------------------ 2: local services */

const TILES = [{ x: 66, y: 34 }, { x: 66, y: 116 }, { x: 234, y: 34 }, { x: 234, y: 116 }]

function ServicesCard({ services, reduced }: { services: { kind: string; name: string; ok: boolean | null; detail: string }[]; reduced: boolean }) {
  const [hover, setHover] = useState(-1)
  const [auto, setAuto] = useState(0)
  useEffect(() => {
    if (reduced || hover >= 0) return
    const t = setInterval(() => setAuto((a) => (a + 1) % TILES.length), 1700)
    return () => clearInterval(t)
  }, [reduced, hover])
  const on = hover >= 0 ? hover : reduced ? -1 : auto
  const up = services.filter((s) => s.ok).length
  const path = (i: number) => {
    const t = TILES[i]
    const dir = t.x < 150 ? -1 : 1
    return `M${150 + dir * 17},75 H${150 + dir * 42} V${t.y} H${t.x - dir * 15}`
  }
  const color = (s?: { ok: boolean | null }) => (s?.ok ? 'var(--color-sev-normal)' : s?.ok === false ? 'var(--color-sev-warning)' : 'var(--color-ink-faint)')
  const shown = on >= 0 ? services[on] : undefined
  return (
    <Frame title="Local services"
      text={<>All of AMC runs on this computer. Monitoring, alerts and End Day need only the database; the language models add conversation and narratives.</>}>
      <svg className="sm-diagram" viewBox="0 0 300 150" role="group" aria-label={`Local services: ${up} of ${services.length} available`}>
        {TILES.map((_, i) => <path key={i} d={path(i)} className={`sm-flow${on === i ? ' sm-flow-on' : ''}`} />)}
        {!reduced && on >= 0 && services[on]?.ok && (
          <circle key={on} r="2.2" className="sm-pkt"><animateMotion dur="1.1s" repeatCount="indefinite" path={path(on)} /></circle>
        )}
        <g transform="translate(133,58)">
          <rect width="34" height="34" rx="7" className="sm-node" />
          <g transform="translate(8,8)" style={{ color: 'var(--color-signal)' }}><Mark size={18} /></g>
        </g>
        {TILES.map((t, i) => {
          const s = services[i]
          return (
            <g key={i} className={`sm-tool${on === i ? ' sm-tool-on' : ''}`} tabIndex={0} role="img"
              aria-label={s ? `${s.name}: ${s.detail}` : 'Loading'}
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(-1)} onFocus={() => setHover(i)} onBlur={() => setHover(-1)}>
              <rect x={t.x - 15} y={t.y - 15} width="30" height="30" rx="6" className="sm-node" style={{ stroke: on === i ? color(s) : undefined }} />
              <g transform={`translate(${t.x},${t.y})`} style={{ color: 'var(--color-ink)' }}><ServiceGlyph kind={s?.kind ?? ''} /></g>
              <circle cx={t.x + 12} cy={t.y - 12} r="3" fill={color(s)} stroke="var(--color-hull)" />
              <text x={t.x} y={t.y + (t.y < 75 ? -21 : 28)} textAnchor="middle" fontSize="8" fill="var(--color-ink-dim)" fontFamily="var(--font-sans)">
                {s?.name ?? ''}
              </text>
            </g>
          )
        })}
        <text x="150" y="112" textAnchor="middle" fontSize="7" fill="var(--color-ink-faint)" fontFamily="var(--font-sans)">
          {shown ? shown.detail.toLowerCase() : `${up} of ${services.length} available`}
        </text>
      </svg>
    </Frame>
  )
}

/* ------------------------------------------------------- 3: written to AMC today */

function ActivityCard({ values, uid }: { values: number[] | undefined; uid: string }) {
  const [ref, inView] = useInView(0.3)
  const [hover, setHover] = useState(-1)
  const W = 300
  const H = 130
  const vals = values && values.length > 1 ? values : Array(24).fill(0)
  const { line, area, points } = chartPaths(vals, W, H, 14)
  const total = vals.reduce((a, b) => a + b, 0)
  const peak = vals.indexOf(Math.max(...vals))
  const i = hover >= 0 ? hover : peak
  const p = points[i]
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    setHover(nearest(points.map((q) => q[0]), ((e.clientX - r.left) / r.width) * W))
  }
  return (
    <div ref={ref} data-in={inView} className="contents">
      <Frame title="Written to AMC today"
        text={<>Companion messages and notes from the whole crew, by hour (UTC). <b className="num text-ink">{total}</b> so far. Recorded values only: AMC does not forecast.</>}>
        <svg className="sm-diagram cursor-crosshair" viewBox={`0 0 ${W} ${H + 6}`} onPointerMove={onMove} onPointerLeave={() => setHover(-1)}
          role="img" aria-label={`Written to AMC today: ${total} entries, busiest at ${String(peak).padStart(2, '0')}:00`}>
          <defs>
            <linearGradient id={`${uid}area`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--color-signal)" stopOpacity=".22" />
              <stop offset="1" stopColor="var(--color-signal)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((g) => <line key={g} x1="14" x2={W - 14} y1={H * g} y2={H * g} stroke="var(--color-seam)" />)}
          <path d={area} fill={`url(#${uid}area)`} />
          <path d={line} fill="none" stroke="var(--color-signal)" strokeWidth="1.5" pathLength={1}
            className={`sm-line-draw${inView ? ' sm-line-in' : ''}`} />
          {[0, 6, 12, 18].map((h) => points[h] && (
            <text key={h} x={points[h][0]} y={H + 4} textAnchor="middle" fontSize="7" fill="var(--color-ink-faint)" fontFamily="var(--font-sans)">
              {String(h).padStart(2, '0')}
            </text>
          ))}
          {hover >= 0 && <line x1={p[0]} x2={p[0]} y1="8" y2={H - 14} stroke="var(--color-ink)" strokeOpacity=".35" />}
          <circle cx={p[0]} cy={p[1]} r="3.2" fill="var(--color-deck)" stroke="var(--color-signal)" strokeWidth="1.5" />
          <g transform={`translate(${clamp(p[0], 40, W - 40)},${p[1] < 34 ? p[1] + 22 : p[1] - 12})`}>
            <rect x="-32" y="-11" width="64" height="15" rx="2" fill="var(--color-ink)" />
            <text y="-1" textAnchor="middle" fontSize="7.5" fill="var(--color-void)" fontFamily="var(--font-sans)">
              {`${String(i).padStart(2, '0')}:00 · ${vals[i]}`}
            </text>
          </g>
        </svg>
      </Frame>
    </div>
  )
}

/* --------------------------------------------------------------- the map */

export function SystemMap() {
  const uid = `sm${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const reduced = useReducedMotion()
  const status = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 20_000, retry: 0 })
  const crew = useQuery({ queryKey: ['doctor', 'crew'], queryFn: doctorApi.crew, refetchInterval: 5_000 })
  const core = useQuery({ queryKey: ['core', 'status'], queryFn: () => request<CoreStatus>('GET', '/am/core/status'), refetchInterval: 15_000 })
  const analytics = useQuery({ queryKey: ['doctor', 'analytics'], queryFn: () => request<Analytics>('GET', '/doctor/analytics'), refetchInterval: 30_000 })

  const o = status.data?.ollama as { reachable: boolean; models: string[]; configured_chat_model: string; configured_embed_model: string } | undefined
  const installed = (m?: string) => !!m && !!o?.models.some((x) => x === m || x.split(':')[0] === m.split(':')[0])
  const model = (m?: string) =>
    !status.data ? null : !o?.reachable ? false : installed(m)
  const modelDetail = (m?: string) =>
    !o?.reachable ? 'Model service not running' : installed(m) ? 'Ready' : 'Not set up'
  const services = [
    { kind: 'database', name: 'Database', ok: status.data ? status.data.database === 'ok' : null, detail: status.data?.database === 'ok' ? 'Online' : String(status.data?.database ?? '…') },
    { kind: 'chat', name: 'Chat model', ok: model(o?.configured_chat_model), detail: modelDetail(o?.configured_chat_model) },
    { kind: 'embed', name: 'Search model', ok: model(o?.configured_embed_model), detail: modelDetail(o?.configured_embed_model) },
    { kind: 'seal', name: 'AMC Core', ok: core.data ? true : null, detail: core.data ? (core.data.open ? 'Seal open' : 'Sealed') : '…' },
  ]

  return (
    <section aria-label="AMC system map" className="sm-root">
      <style>{SM_CSS}</style>
      <div className="sm-bento">
        <FlowCard crew={crew.data ?? []} core={core.data} uid={uid} reduced={reduced} />
        <ServicesCard services={services} reduced={reduced} />
        <ActivityCard values={analytics.data?.activity.written[0]} uid={uid} />
      </div>
    </section>
  )
}

const SM_CSS = `
.sm-root{container-type:inline-size}
.sm-bento{display:grid;grid-template-columns:minmax(0,1fr);gap:28px;padding:6px}
@container (min-width:720px){.sm-bento{grid-template-columns:repeat(2,minmax(0,1fr))}.sm-wide{grid-column:1 / -1}}
.sm-frame{position:relative;display:flex;flex-direction:column}
.sm-card{flex:1;display:flex;flex-direction:column;gap:14px;padding:clamp(14px,2cqw,22px);background:linear-gradient(180deg,var(--color-deck),var(--color-hull));border:1px solid var(--color-seam);border-radius:4px;transition:border-color .3s}
.sm-frame:hover .sm-card{border-color:var(--color-seam-strong)}
.sm-c{position:absolute;width:12px;height:12px;border:0 solid var(--color-seam-strong);pointer-events:none;transition:border-color .3s,transform .35s cubic-bezier(.2,.8,.2,1)}
.sm-c-tl{top:-6px;left:-6px;border-top-width:1.5px;border-left-width:1.5px}
.sm-c-tr{top:-6px;right:-6px;border-top-width:1.5px;border-right-width:1.5px}
.sm-c-bl{bottom:-6px;left:-6px;border-bottom-width:1.5px;border-left-width:1.5px}
.sm-c-br{bottom:-6px;right:-6px;border-bottom-width:1.5px;border-right-width:1.5px}
.sm-frame:hover>.sm-c{border-color:var(--color-signal)}
.sm-frame:hover>.sm-c-tl{transform:translate(-3px,-3px)}
.sm-frame:hover>.sm-c-tr{transform:translate(3px,-3px)}
.sm-frame:hover>.sm-c-bl{transform:translate(-3px,3px)}
.sm-frame:hover>.sm-c-br{transform:translate(3px,3px)}
.sm-diagram{display:block;width:100%;height:auto;overflow:visible}
.sm-wide .sm-diagram{max-width:720px;margin:0 auto}
.sm-flow{stroke:var(--color-seam-strong);fill:none;stroke-width:1.2;transition:stroke .3s}
.sm-flow-on{stroke:var(--color-signal)}
.sm-dash{stroke-dasharray:3 4;animation:sm-dash 1.2s linear infinite}
.sm-pkt{fill:var(--color-signal)}
.sm-node{fill:var(--color-deck);stroke:var(--color-seam-strong);transition:stroke .3s}
.sm-av{cursor:default;outline:none;transition:transform .35s cubic-bezier(.2,.8,.2,1);transform-box:fill-box;transform-origin:center}
.sm-av:hover,.sm-av:focus-visible{transform:scale(1.15)}
.sm-tip{opacity:0;transition:opacity .2s;pointer-events:none}
.sm-av:hover .sm-tip,.sm-av:focus .sm-tip{opacity:1}
.sm-tool{cursor:default;outline:none;transition:transform .3s cubic-bezier(.2,.8,.2,1);transform-box:fill-box;transform-origin:center}
.sm-tool:hover,.sm-tool-on{transform:translateY(-2px)}
.sm-pulse{animation:sm-pulse 2s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
.sm-sheet-new{animation:sm-sheet-in .7s cubic-bezier(.2,.8,.2,1) both}
.sm-line-draw{stroke-dasharray:1;stroke-dashoffset:1;transition:stroke-dashoffset 1.8s cubic-bezier(.4,.1,.2,1)}
.sm-line-in{stroke-dashoffset:0}
@keyframes sm-dash{to{stroke-dashoffset:-14}}
@keyframes sm-pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes sm-sheet-in{from{opacity:0;transform:translate(14px,-10px) rotate(4deg)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.sm-dash,.sm-pulse,.sm-sheet-new{animation:none}.sm-line-draw{transition:none;stroke-dashoffset:0}}
`
