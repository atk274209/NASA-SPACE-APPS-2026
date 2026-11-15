// SIMULATED space events, as they happen: a card slides in for each new event with what happened
// and what AMC did about it. For the doctor, the System page also has the controls and the history.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { demoApi, EVENT_COLOR, type SpaceEvent } from '../api/demo'
import { fmtTime, missionNow, parseUtc } from '../lib/format'
import { Section } from './ui'

const SEEN_KEY = 'am.demo.seen'

function readSeen(): number[] {
  try { return JSON.parse(sessionStorage.getItem(SEEN_KEY) ?? '[]') } catch { return [] }
}

function EventBody({ e }: { e: SpaceEvent }) {
  return (
    <>
      <p className="text-sm text-ink first-letter:uppercase">{e.summary.replace(/^SIMULATED:\s*/, '')}</p>
      {e.reactions.length > 0 && (
        <ul className="mt-2 grid gap-1 text-sm text-ink-dim">
          {e.reactions.map((r, i) => (
            <li key={i} className="flex gap-2"><span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-ink-faint" />{r}</li>
          ))}
        </ul>
      )}
    </>
  )
}

/** Toasts for new events. Shown to both roles; the server filters what a crew member may see. */
export function SpaceEventToasts() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['demo', 'events'], queryFn: () => demoApi.events(10), refetchInterval: 4_000, retry: 0 })
  const [seen, setSeen] = useState<number[]>(readSeen)
  const [baseline, setBaseline] = useState<number | null>(null)
  // On first load, events older than two minutes count as already seen (no flood after a reload).
  useEffect(() => {
    if (!data || baseline !== null) return
    const cutoff = missionNow() - 120_000
    const old = data.events.filter((e) => parseUtc(e.started_at).getTime() < cutoff).map((e) => e.id)
    setBaseline(1)
    if (old.length) setSeen((s) => [...new Set([...s, ...old])])
  }, [data, baseline])
  useEffect(() => { try { sessionStorage.setItem(SEEN_KEY, JSON.stringify(seen.slice(-200))) } catch { /* private mode */ } }, [seen])
  const fresh = (data?.events ?? []).filter((e) => !seen.includes(e.id) && e.status === 'active').slice(0, 3)
  const dismiss = (id: number) => {
    setSeen((s) => [...s, id])
    qc.invalidateQueries({ queryKey: ['alerts'] })
  }
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-40 grid w-[min(26rem,calc(100vw-2rem))] gap-3">
      <AnimatePresence>
        {fresh.map((e) => (
          <motion.section
            key={e.id}
            role="status"
            aria-label={`Simulated event: ${e.title}`}
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            transition={{ duration: 0.25 }}
            className="pointer-events-auto rounded-md border border-seam-strong bg-hull p-4 shadow-[0_12px_32px_-12px_rgba(0,0,0,.6)]"
            style={{ borderLeft: `3px solid ${EVENT_COLOR[e.severity]}` }}
          >
            <header className="mb-1.5 flex items-start gap-3">
              <div className="min-w-0">
                <h2 className="font-semibold text-ink">{e.title}</h2>
                <p className="text-xs text-ink-faint">
                  Simulated event{e.astronaut_name ? ` for ${e.astronaut_name}` : ' for the whole crew'}, {fmtTime(e.started_at)}
                </p>
              </div>
              <button onClick={() => dismiss(e.id)} aria-label="Dismiss"
                className="ml-auto grid size-7 shrink-0 place-items-center rounded text-ink-dim hover:bg-deck hover:text-ink">
                <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              </button>
            </header>
            <EventBody e={e} />
          </motion.section>
        ))}
      </AnimatePresence>
    </div>
  )
}

/** Doctor's controls and history, on the System page. */
export function SpaceEventsPanel() {
  const qc = useQueryClient()
  const events = useQuery({ queryKey: ['demo', 'events', 'all'], queryFn: () => demoApi.events(30), refetchInterval: 5_000 })
  const kinds = useQuery({ queryKey: ['demo', 'kinds'], queryFn: demoApi.kinds, staleTime: Infinity })
  const refresh = () => qc.invalidateQueries({ queryKey: ['demo'] })
  const trigger = useMutation({ mutationFn: (kind?: string) => demoApi.trigger(kind), onSuccess: refresh })
  const auto = useMutation({ mutationFn: (on: boolean) => demoApi.auto(on), onSuccess: refresh })
  const d = events.data
  if (!d?.available) return null
  return (
    <Section title="Simulated space events"
      aside={<span>Every {Math.round(d.interval_s / 60)} min while automatic events are on</span>}>
      <div className="grid gap-4">
        <p className="max-w-[72ch] text-sm text-ink-dim">
          Each event changes one real input (a cabin sensor, wearable readings, a symptom report). What follows
          is AMC&apos;s own reaction, not a script. Everything an event creates is marked simulated.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => trigger.mutate(undefined)} disabled={trigger.isPending}
            className="h-9 rounded-md bg-ink px-4 text-sm font-semibold text-void disabled:opacity-50">Start a random event</button>
          <label className="ml-2 inline-flex items-center gap-2 text-sm text-ink-dim">
            <input type="checkbox" checked={d.auto} onChange={(e) => auto.mutate(e.target.checked)} disabled={auto.isPending}
              className="size-4 accent-[var(--color-signal)]" />
            Automatic events
          </label>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {kinds.data?.map((k) => (
            <button key={k.code} onClick={() => trigger.mutate(k.code)} disabled={trigger.isPending}
              className="h-8 rounded border border-seam-strong px-2.5 text-sm text-ink hover:border-signal disabled:opacity-50">{k.title}</button>
          ))}
        </div>
        <ol className="grid gap-3">
          {d.events.map((e) => (
            <li key={e.id} className="rounded-md border border-seam bg-hull/80 p-3" style={{ borderLeft: `3px solid ${EVENT_COLOR[e.severity]}` }}>
              <div className="mb-1 flex flex-wrap items-baseline gap-x-3 text-sm">
                <span className="font-semibold text-ink">{e.title}</span>
                <span className="text-ink-faint">{e.astronaut_name ?? 'Whole crew'}</span>
                <span className="num ml-auto text-xs text-ink-faint">{fmtTime(e.started_at)}{e.status === 'active' ? ', ongoing' : ', ended'}</span>
              </div>
              <EventBody e={e} />
            </li>
          ))}
          {!d.events.length && <li className="text-sm text-ink-faint">No events yet. The first one starts a few seconds after AMC starts.</li>}
        </ol>
      </div>
    </Section>
  )
}
