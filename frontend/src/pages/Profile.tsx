// Profile (avatar menu): who is signed in, and sign out.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { request } from '../api/client'
import { useAuth } from '../state/auth'
import { Section } from '../components/ui'

export function ProfilePage() {
  const { user, signOut, signingOut } = useAuth()
  return (
    <div className="grid max-w-xl gap-6">
      <Section title="Profile">
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-ink-dim">Name</dt><dd>{user.display_name}</dd>
          <dt className="text-ink-dim">Username</dt><dd className="num">{user.username}</dd>
          <dt className="text-ink-dim">Role</dt><dd>{user.role === 'doctor' ? 'Doctor (flight surgeon)' : 'Astronaut'}</dd>
          {user.is_test_account && <><dt className="text-ink-dim">Account</dt><dd>Test account (fictional crew)</dd></>}
        </dl>
      </Section>
      {user.role === 'astronaut' && user.astronaut_id && <LeaderboardChoice astronautId={user.astronaut_id} />}
      <Section title="Privacy">
        <p className="text-sm text-ink-dim">
          {user.role === 'astronaut'
            ? 'Your records are visible to you and to the medical officer account only. Everything you write to AMC reaches the medical officer\'s feed. AMC also keeps its own sealed mission log (health, behaviour, crew): nobody reads it unless the medical officer opens it in an emergency, which you are told about, or after the mission returns.'
            : 'You can read every crew member\'s medical records. Your reads of crew records, acknowledgements and approvals are written to the audit log.'}
        </p>
      </Section>
      <button onClick={signOut} disabled={signingOut}
        className="h-10 justify-self-start rounded-md border border-seam-strong px-4 text-sm hover:border-signal">
        {signingOut ? 'Signing out…' : 'Sign out'}
      </button>
    </div>
  )
}

function LeaderboardChoice({ astronautId }: { astronautId: number }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['optin', astronautId], queryFn: () => request<{ opt_in: boolean }>('GET', '/profile/leaderboard', undefined, { astronaut_id: astronautId }) })
  const m = useMutation({
    mutationFn: (v: boolean) => request<{ opt_in: boolean }>('POST', '/profile/leaderboard', { astronaut_id: astronautId, opt_in: v }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['optin', astronautId] }); qc.invalidateQueries({ queryKey: ['leaderboard'] }) },
  })
  if (!q.data) return null
  return (
    <Section title="Crew hydration board">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={q.data.opt_in} disabled={m.isPending} onChange={(e) => m.mutate(e.target.checked)} />
        Show me on the crew board (adherence only, never amounts)
      </label>
    </Section>
  )
}
