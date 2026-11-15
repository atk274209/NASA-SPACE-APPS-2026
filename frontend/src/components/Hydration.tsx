// Water and vitamins (DEMO target), mission-completion streaks and the crew hydration leaderboard.
// The leaderboard ranks adherence capped at 100 %: drinking more than the target earns nothing extra.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { request } from '../api/client'
import { streaksApi } from '../api/streaks'
import { useAuth } from '../state/auth'
import { ErrorNote } from './ui'

interface NutritionToday {
  mission_day: number
  label: string
  target_ml: number
  total_ml: number
  progress: number
  target_met: boolean
  quick_add_ml: number[]
  entries: { id: number; amount_ml: number; source: string; logged_at: string }[]
  supplements: { code: string; label: string; taken: boolean }[]
}
interface BoardRow { astronaut_id: number; name: string; code: string; days_met: number; days_counted: number; adherence_pct: number; streak: number; rank: number }
interface Board { window_days: number; label: string; rule: string; rows: BoardRow[] }

const api = {
  today: (id: number) => request<NutritionToday>('GET', '/nutrition/today', undefined, { astronaut_id: id }),
  water: (id: number, ml: number) => request<NutritionToday>('POST', '/nutrition/water', { astronaut_id: id, amount_ml: ml }),
  undo: (entryId: number) => request<NutritionToday>('POST', `/nutrition/water/${entryId}/undo`),
  supplement: (id: number, code: string, taken: boolean) =>
    request<NutritionToday>('POST', '/nutrition/supplements', { astronaut_id: id, supplement_code: code, taken }),
  board: () => request<Board>('GET', '/nutrition/leaderboard'),
}

const CUP_NAMES: Record<number, string> = { 150: 'cup', 250: 'glass', 500: 'bottle' }

function Glass({ progress, met }: { progress: number; met: boolean }) {
  return (
    <svg viewBox="0 0 60 84" className="h-24 w-16 shrink-0" aria-hidden>
      <defs>
        <clipPath id="glass-clip"><path d="M6 4h48l-6 74a4 4 0 01-4 4H16a4 4 0 01-4-4z" /></clipPath>
      </defs>
      <g clipPath="url(#glass-clip)">
        <rect x="0" y="0" width="60" height="84" fill="var(--color-deck)" />
        <motion.rect x="0" width="60" height="84" fill={met ? 'var(--color-sev-normal)' : 'var(--color-signal)'} opacity={0.75}
          initial={false} animate={{ y: 84 - 84 * progress }} transition={{ type: 'spring', stiffness: 90, damping: 16 }} />
        <motion.path d="M0 0 Q15 -4 30 0 T60 0 V6 H0z" fill={met ? 'var(--color-sev-normal)' : 'var(--color-signal)'} opacity={0.9}
          initial={false} animate={{ y: 84 - 84 * progress - 3 }} transition={{ type: 'spring', stiffness: 90, damping: 16 }} />
      </g>
      <path d="M6 4h48l-6 74a4 4 0 01-4 4H16a4 4 0 01-4-4z" fill="none" stroke="var(--color-seam-strong)" strokeWidth="2" />
    </svg>
  )
}

export function HydrationCard({ astronautId }: { astronautId: number }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['nutrition', astronautId], queryFn: () => api.today(astronautId), refetchInterval: 30_000 })
  const refresh = (d: NutritionToday) => {
    qc.setQueryData(['nutrition', astronautId], d)
    qc.invalidateQueries({ queryKey: ['leaderboard'] })
  }
  const add = useMutation({ mutationFn: (ml: number) => api.water(astronautId, ml), onSuccess: refresh })
  const undo = useMutation({ mutationFn: (id: number) => api.undo(id), onSuccess: refresh })
  const supp = useMutation({ mutationFn: ({ code, taken }: { code: string; taken: boolean }) => api.supplement(astronautId, code, taken), onSuccess: refresh })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return null
  const d = q.data
  const last = d.entries[d.entries.length - 1]
  return (
    <section className="rounded-lg border border-seam bg-hull p-4">
      <h2 className="text-sm font-semibold text-ink">Water and vitamins <span className="font-normal text-ink-faint">· day {d.mission_day}</span></h2>
      <div className="mt-3 flex items-center gap-4">
        <Glass progress={d.progress} met={d.target_met} />
        <div className="grid gap-2">
          <p className="num text-2xl text-ink">
            {(d.total_ml / 1000).toFixed(2)} <span className="text-base text-ink-dim">/ {(d.target_ml / 1000).toFixed(1)} L</span>
          </p>
          <p className="text-xs text-ink-dim" title={d.label}>
            {d.target_met ? 'Target reached for today. Nice.' : `${Math.max(0, Math.round((d.target_ml - d.total_ml) / 50) * 50)} mL to go`}
            <span className="text-ink-faint"> · DEMO target</span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {d.quick_add_ml.map((ml) => (
              <motion.button key={ml} whileTap={{ scale: 0.92 }} disabled={add.isPending} onClick={() => add.mutate(ml)}
                className="rounded-full border border-signal/60 bg-signal-dim/40 px-3 py-1 text-sm text-ink hover:bg-signal-dim">
                +{ml} mL {CUP_NAMES[ml] ? <span className="text-ink-dim">{CUP_NAMES[ml]}</span> : null}
              </motion.button>
            ))}
            {last && (
              <button className="px-2 text-xs text-ink-dim hover:text-ink" disabled={undo.isPending} onClick={() => undo.mutate(last.id)}>
                undo last ({last.amount_ml} mL)
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="mt-4">
        <p className="mb-1.5 text-xs text-ink-dim">Supplements from your mission protocol: tap what you took today</p>
        <div className="flex flex-wrap gap-1.5">
          {d.supplements.map((s) => (
            <motion.button key={s.code} whileTap={{ scale: 0.94 }} aria-pressed={s.taken}
              onClick={() => supp.mutate({ code: s.code, taken: !s.taken })}
              className={`rounded-full border px-3 py-1 text-sm ${s.taken ? 'border-sev-normal bg-sev-normal/20 text-ink' : 'border-seam-strong text-ink-dim hover:text-ink'}`}>
              {s.taken ? '✓ ' : ''}{s.label.replace(/ \(.*\)$/, '')}
            </motion.button>
          ))}
        </div>
        <p className="mt-1 text-xs text-ink-faint">AM records what you took; it never suggests a supplement or a dose.</p>
      </div>
      {(add.error || undo.error || supp.error) && <div className="mt-2"><ErrorNote error={add.error ?? undo.error ?? supp.error} /></div>}
    </section>
  )
}

const STREAK_SHOWN = ['missions_complete', 'checkin', 'hydration_logged']

export function StreakChips({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['streaks', astronautId], queryFn: () => streaksApi.get(astronautId), refetchInterval: 60_000 })
  if (!q.data) return null
  const items = q.data.streaks.filter((s) => STREAK_SHOWN.includes(s.code))
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((s) => (
        <span key={s.code} title={s.rule}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm ${s.current > 0 ? 'border-sev-watch/60 text-ink' : 'border-seam-strong text-ink-dim'}`}>
          <svg viewBox="0 0 24 24" className="size-4" fill={s.current > 0 ? 'var(--color-sev-watch)' : 'none'} stroke="currentColor" strokeWidth="1.5" aria-hidden>
            <path d="M12 3c1 4 5 5 5 10a5 5 0 01-10 0c0-3 2-4 2-7 1 1 2 2 3 4 0-3 0-5 0-7z" />
          </svg>
          <span className="num">{s.current}</span> {s.code === 'missions_complete' ? 'missions' : s.code === 'checkin' ? 'check-in' : 'hydration'}
          {s.at_risk && <span className="text-xs text-sev-watch">· today keeps it</span>}
        </span>
      ))}
    </div>
  )
}

export function HydrationLeaderboard() {
  const { user } = useAuth()
  const q = useQuery({ queryKey: ['leaderboard'], queryFn: api.board, refetchInterval: 30_000 })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return null
  return (
    <section className="rounded-lg border border-seam bg-hull p-4">
      <h2 className="text-sm font-semibold text-ink">Crew hydration board <span className="font-normal text-ink-faint">· last {q.data.window_days} days</span></h2>
      <p className="mb-2 text-xs text-ink-faint">{q.data.rule}</p>
      <ol className="grid gap-1.5">
        {q.data.rows.map((r) => {
          const me = r.astronaut_id === user.astronaut_id
          return (
            <li key={r.astronaut_id} className={`grid grid-cols-[1.5rem_1fr_auto] items-center gap-2 rounded px-2 py-1.5 text-sm ${me ? 'bg-signal-dim/40' : 'bg-deck'}`}>
              <span className="num text-center text-ink-dim">{r.rank}</span>
              <span className="min-w-0">
                <span className={me ? 'font-semibold text-ink' : 'text-ink'}>{me ? 'You' : r.name}</span>
                <span className="mt-1 block h-1.5 overflow-hidden rounded bg-void">
                  <motion.span className="block h-full rounded bg-signal" initial={{ width: 0 }} animate={{ width: `${r.adherence_pct}%` }} />
                </span>
              </span>
              <span className="num text-xs text-ink-dim">{r.days_met}/{r.days_counted} days · {r.adherence_pct}%{r.streak > 0 ? ` · ${r.streak}🔥` : ''}</span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
