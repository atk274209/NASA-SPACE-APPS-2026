import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api/client'
import { doctorApi } from '../api/doctor'
import { useAuth } from '../state/auth'
import { useSession } from '../state/session'
import { useIntercomUnread } from '../pages/Intercom'
import { SpaceEventToasts } from './SpaceEvents'
import { AmMark, SimulatedMark } from './ui'

// Simple role-based navigation: 3 items for an astronaut, doctor items for the medical officer.
// Every earlier page is still reachable: grouped as tabs inside Health and Settings.
const ASTRONAUT_NAV = [
  { to: '/', label: 'Companion', icon: 'M4 5h16v11H9l-5 4V5zm4 5h8m-8 3h5' },
  { to: '/intercom', label: 'Intercom', icon: 'M4 15V9a2 2 0 012-2h3l4-3v16l-4-3H6a2 2 0 01-2-2zm13-6a4 4 0 010 6m2-9a8 8 0 010 12' },
  { to: '/diary', label: 'Diary', icon: 'M7 3h10a2 2 0 012 2v16l-7-4-7 4V5a2 2 0 012-2z' },
  { to: '/health', label: 'Health', icon: 'M4 19V9m6 10V5m6 14v-7m4 7H2' },
  { to: '/journal', label: 'Journal', icon: 'M6 3h11a2 2 0 012 2v16H8a2 2 0 01-2-2V3zm0 16a2 2 0 012-2h11M10 7h6M10 11h6' },
]
const DOCTOR_NAV = [
  { to: '/', label: 'Crew', icon: 'M9 11a4 4 0 100-8 4 4 0 000 8zm-7 10v-1a6 6 0 0112 0v1m3-10a3 3 0 100-6m4 16v-1a5 5 0 00-3-4.6' },
  { to: '/intercom', label: 'Intercom', icon: 'M4 15V9a2 2 0 012-2h3l4-3v16l-4-3H6a2 2 0 01-2-2zm13-6a4 4 0 010 6m2-9a8 8 0 010 12' },
  { to: '/am', label: 'AM Core', icon: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 5a4 4 0 100 8 4 4 0 000-8zm0 3v2' },
  { to: '/environment', label: 'Environment', icon: 'M12 3v10m0 0a4 4 0 104 4M8 7h2m-2 3h2M3 21h18' },
  { to: '/alerts', label: 'Alerts', icon: 'M12 3l9 16H3L12 3zm0 6v4m0 3v.5' },
  { to: '/settings', label: 'Settings', icon: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4' },
]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  )
}

function IntercomBadge() {
  const n = useIntercomUnread()
  if (!n) return null
  return <span className="num ml-auto rounded-sm bg-signal/20 px-1.5 text-xs text-signal" title="Unread intercom messages">{n}</span>
}

function UnseenRedCount() {
  const { data } = useQuery({ queryKey: ['doctor', 'crew'], queryFn: doctorApi.crew, refetchInterval: 5_000 })
  const n = data?.reduce((s, c) => s + c.unseen_red, 0) ?? 0
  if (!n) return null
  return <span className="num ml-auto rounded-sm bg-sev-critical/20 px-1.5 text-xs text-sev-critical" title="Unseen red flags">{n}</span>
}

function OpenAlertCount() {
  const { isDoctor } = useAuth()
  const { astronaut } = useSession()
  const scope = isDoctor ? undefined : astronaut?.id
  const { data } = useQuery({
    queryKey: ['alerts', scope ?? 'crew', 'open'],
    queryFn: () => api.alerts({ astronaut_id: scope, open_only: true }),
    enabled: isDoctor || !!astronaut,
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
    <div className="flex shrink-0 items-center gap-4 text-sm text-ink-dim">
      <span className="inline-flex items-center gap-1.5" title="Local AM service and database">
        <span className="size-2 rounded-full" style={{ background: backendOk ? 'var(--color-sev-normal)' : 'var(--color-sev-critical)' }} />
        <span className="hidden sm:inline">Core {backendOk ? 'online' : 'unreachable'}</span>
      </span>
      <span className="inline-flex items-center gap-1.5" title="Local language model (Ollama). Monitoring works without it.">
        <span className="size-2 rounded-full" style={{ background: llmState === 'ready' ? 'var(--color-sev-normal)' : 'var(--color-ink-faint)' }} />
        <span className="hidden sm:inline">AI {llmState}</span>
      </span>
    </div>
  )
}

function UserMenu() {
  const { user } = useAuth()
  const { astronaut } = useSession()
  const initials = user.display_name.split(' ').filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join('')
  return (
    <div className="flex min-w-0 items-center gap-3">
      {user.role === 'astronaut' && astronaut?.is_simulated && <SimulatedMark title="Your records in this prototype are SIMULATED test data." />}
      <NavLink to="/profile" className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 hover:bg-deck" title="Profile and sign out">
        <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-signal-dim text-sm font-semibold text-ink">{initials || '?'}</span>
        <span className="hidden min-w-0 truncate text-sm sm:inline">{user.display_name}</span>
      </NavLink>
    </div>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const { isDoctor } = useAuth()
  const nav = isDoctor ? DOCTOR_NAV : ASTRONAUT_NAV
  return (
    <div className="flex min-h-screen">
      <nav aria-label="Primary" className="sticky top-0 flex h-screen w-14 shrink-0 flex-col border-r border-seam bg-hull md:w-48">
        <div className="flex h-14 items-center gap-2 border-b border-seam px-4">
          <AmMark className="size-6 shrink-0" />
          <span className="hidden text-lg font-bold md:inline" style={{ fontStretch: '125%' }}>AM</span>
        </div>
        <ul className="flex flex-col gap-0.5 p-2">
          {nav.map((n) => (
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
                {n.to === '/intercom' && <span className="hidden md:contents"><IntercomBadge /></span>}
                {isDoctor && n.to === '/' && <span className="hidden md:contents"><UnseenRedCount /></span>}
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
          <ServiceLights />
          <UserMenu />
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-6">{children}</main>
        <SpaceEventToasts />
      </div>
    </div>
  )
}
