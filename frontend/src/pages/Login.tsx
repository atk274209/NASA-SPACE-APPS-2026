import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { authApi, type AuthState } from '../api/auth'
import { AmMark, ErrorNote } from '../components/ui'

const inputCls = 'h-10 rounded-md border border-seam-strong bg-deck px-3 text-ink outline-none focus:border-signal'

export function LoginPage({ state }: { state: AuthState }) {
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: () => authApi.login(username.trim(), password),
    // Reset (not just invalidate) every query: nothing cached for a previous user survives,
    // and the active auth query refetches at once.
    onSuccess: () => qc.resetQueries(),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate()
  }
  return (
    <main className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-md">
        <div className="mb-10">
          <AmMark className="mb-6 size-10" />
          <h1 className="text-[2rem] text-ink">Allied Mastercomputer</h1>
          <p className="mt-2 text-ink-dim">Crew health on board. Sign in with your crew account.</p>
        </div>
        <form onSubmit={submit} className="grid gap-4 border-t border-seam-strong pt-6">
          <label className="grid gap-1 text-sm">
            <span className="text-ink-dim">Username</span>
            <input className={inputCls} autoComplete="username" autoFocus value={username}
              onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="grid gap-1 text-sm">
            <span className="text-ink-dim">Password</span>
            <input className={inputCls} type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button
            className="h-10 rounded-md bg-ink font-semibold text-void hover:bg-white disabled:opacity-50"
            disabled={!username.trim() || !password || login.isPending}
          >
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </button>
          {login.error && <ErrorNote error={login.error} />}
        </form>
        {state.test_accounts && (
          <p className="mt-4 text-xs leading-relaxed text-ink-faint">
            Test accounts exist (FICTIONAL crew).
            {state.test_accounts_file && <> Sign-in details: <span className="num break-all">{state.test_accounts_file}</span></>}
          </p>
        )}
        <p className="mt-6 text-xs text-ink-faint">Research prototype. Not a medical device. Works fully offline.</p>
      </div>
    </main>
  )
}
