export type SeverityName = 'NORMAL' | 'WATCH' | 'WARNING' | 'CRITICAL'

export const SEVERITY_RANK: Record<string, number> = { NORMAL: 0, WATCH: 1, WARNING: 2, CRITICAL: 3 }

export const SEVERITY_COLOR: Record<string, string> = {
  NORMAL: 'var(--color-sev-normal)',
  WATCH: 'var(--color-sev-watch)',
  WARNING: 'var(--color-sev-warning)',
  CRITICAL: 'var(--color-sev-critical)',
}

export function severityColor(sev: string | null | undefined): string {
  return (sev && SEVERITY_COLOR[sev]) || 'var(--color-ink-faint)'
}

/** Backend stores naive UTC; mark it as UTC before parsing. */
export function parseUtc(ts: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts}Z`)
}

const dtf = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const tf = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })

export const fmtDateTime = (ts: string) => dtf.format(parseUtc(ts))
export const fmtTime = (ts: string) => tf.format(parseUtc(ts))

export function fmtAgo(ts: string, now = Date.now()): string {
  const s = Math.round((now - parseUtc(ts).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  return Number.isInteger(v) && digits <= 1 ? String(v) : v.toFixed(digits)
}

export function fmtZ(z: number | null | undefined): string {
  if (z === null || z === undefined) return '–'
  return `${z >= 0 ? '+' : '−'}${Math.abs(z).toFixed(1)}σ`
}

const UNIT_LABEL: Record<string, string> = { degC: '°C', count: 'steps' }
export const unitLabel = (u: string) => UNIT_LABEL[u] ?? u

const CONTEXT_LABEL: Record<string, string> = {
  rest: 'Resting',
  exercise: 'Exercise',
  sleep: 'Sleep',
  other: 'Other',
  daily: 'Daily total',
  fasting: 'Fasting',
  postprandial: 'After a meal',
}
export const contextLabel = (c: string) => CONTEXT_LABEL[c] ?? c

export const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
