import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, type ReactNode } from 'react'
import { authApi, type User } from '../api/auth'
import { ErrorNote } from '../components/ui'
import { syncMissionClock } from '../lib/format'
import { LoginPage } from '../pages/Login'

interface AuthValue {
  user: User
  isDoctor: boolean
  signOut: () => void
  signingOut: boolean
}

const AuthContext = createContext<AuthValue | null>(null)

/** Shows the sign-in page until the backend reports a session; then provides the signed-in user. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const state = useQuery({
    queryKey: ['auth'],
    queryFn: async () => {
      const s = await authApi.state()
      syncMissionClock(s.mission_now)
      return s
    },
    retry: 0,
    refetchInterval: 60_000, // keeps the mission clock in step after an End Day in the Test Console
  })
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSettled: () => qc.resetQueries(), // never show the previous user's data to the next one
  })
  if (state.isLoading) return <p className="p-6 text-ink-dim">Connecting to AMC…</p>
  if (state.error) return <div className="p-6"><ErrorNote error={state.error} /></div>
  if (!state.data?.signed_in || !state.data.user) return <LoginPage state={state.data!} />
  const user = state.data.user
  return (
    <AuthContext.Provider value={{ user, isDoctor: user.role === 'doctor', signOut: () => logout.mutate(), signingOut: logout.isPending }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext)
  if (!v) throw new Error('useAuth outside AuthGate')
  return v
}
