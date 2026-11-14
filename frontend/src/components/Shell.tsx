import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api/client'
import { useSession } from '../state/session'
import { SimulatedMark } from './ui'

const NAV = [
  { to: '/', label: 'Overview', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { to: '/companion', label: 'Companion', icon: 'M4 5h16v11H9l-5 4V5zm4 5h8m-8 3h5' },
  { to: '/health', label: 'Health', icon: 'M4 19V9m6 10V5m6 14v-7m4 7H2' },
  { to: '/baselines', label: 'Baselines', icon: 'M3 12h18M3 7h18M3 17h18' },
  { to: '/alerts', label: 'Alerts', icon: 'M12 3l9 16H3L12 3zm0 6v4m0 3v.5' },
  { to: '/system', label: 'System', icon: 'M12 8a4 4 0 100 8 4 4 0 000-8zm0-5v3m0 12v3M3 12h3m12 0h3' },
]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  )
}

function OpenAlertCount() {
  const { astronaut } = useSession()
  const { data } = useQuery({
    queryKey: ['alerts', astronaut?.id, 'open'],
    queryFn: () => api.alerts({ astronaut_id: astronaut!.id, open_only: true }),
    enabled: !!astronaut,
    refetchInterval: 15_000,
  })
  if (!data?.length) return null
  return (
    <span className="num ml-auto rounded-sm bg-sev-critical/20 px-1.5 text-xs text-sev-critical">{data.length}</span>
  )
}

function ServiceLights() {
  const { data, isError } = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 20_000, retry: 0 })
  const backendOk = !isError && data?.status === 'ok'
  const llm = data?.ollama as { reachable?: boolean; models?: string[] } | undefined
  const llmState = !llm?.reachable ? 'offline' : llm.models?.length ? 'ready' : 'no model'
  return (
    <div className="flex items-center gap-4 text-sm text-ink-dim">
      <span className="inline-flex items-center gap-1.5" title="Local AM service and database">
        <span className="size-2 rounded-full" style={{ background: backendOk ? 'var(--color-sev-normal)' : 'var(--color-sev-critical)' }} />
        Core {backendOk ? 'online' : 'unreachable'}
      </span>
      <span className="inline-flex items-center gap-1.5" title="Local language model (Ollama). Monitoring works without it.">
        <span className="size-2 rounded-full" style={{ background: llmState === 'ready' ? 'var(--color-sev-normal)' : 'var(--color-ink-faint)' }} />
        AI {llmState}
      </span>
    </div>
  )
}

function AstronautPicker() {
  const { astronauts, astronaut, selectAstronaut } = useSession()
  if (!astronauts.length) return <span className="text-sm text-ink-faint">No crew records</span>
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="crew" className="sr-only">Crew member</label>
      <select
        id="crew"
        value={astronaut?.id ?? ''}
        onChange={(e) => selectAstronaut(Number(e.target.value))}
        className="rounded-md border border-seam-strong bg-deck px-2 py-1 text-sm text-ink focus:border-signal focus:outline-none"
      >
        {astronauts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.display_name} ({a.code})
          </option>
        ))}
      </select>
      {astronaut?.is_simulated && <SimulatedMark />}
    </div>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <nav aria-label="Primary" className="sticky top-0 flex h-screen w-14 shrink-0 flex-col border-r border-seam bg-hull md:w-48">
        <div className="flex h-14 items-center gap-2 border-b border-seam px-4">
          <svg viewBox="0 0 32 32" className="size-6 shrink-0" aria-hidden>
            <path d="M4 20h5l2.5-7 4 12 3-9 1.5 4H28" fill="none" stroke="var(--color-signal)" strokeWidth="2.4"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="hidden text-lg font-semibold tracking-wide md:inline">AM</span>
        </div>
        <ul className="flex flex-col gap-0.5 p-2">
          {NAV.map((n) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-md px-2.5 py-2 text-[15px] transition-colors ${
                    isActive ? 'bg-deck text-ink shadow-[inset_2px_0_0_var(--color-signal)]' : 'text-ink-dim hover:bg-deck/60 hover:text-ink'
                  }`
                }
              >
                <Icon d={n.icon} />
                <span className="hidden md:inline">{n.label}</span>
                {n.to === '/alerts' && <span className="hidden md:contents"><OpenAlertCount /></span>}
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="mt-auto hidden p-4 text-xs leading-relaxed text-ink-faint md:block">
          Research prototype. Not a medical device.
        </p>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-seam bg-void/95 px-4 backdrop-blur md:px-6">
          <AstronautPicker />
          <ServiceLights />
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  )
}
