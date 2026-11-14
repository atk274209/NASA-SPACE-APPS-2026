import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type Astronaut } from '../api/client'

function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}
function writeLocal(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: selection just won't persist */
  }
}

interface SessionValue {
  astronauts: Astronaut[]
  astronautsLoading: boolean
  astronaut: Astronaut | null
  selectAstronaut: (id: number) => void
  operator: string
  setOperator: (name: string) => void
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data: astronauts = [], isLoading } = useQuery({ queryKey: ['astronauts'], queryFn: api.astronauts })
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const v = readLocal('am.astronaut')
    return v ? Number(v) : null
  })
  const [operator, setOperatorState] = useState(() => readLocal('am.operator') ?? '')

  // Fall back to the most recently created astronaut when the stored one no longer exists.
  useEffect(() => {
    if (astronauts.length && !astronauts.some((a) => a.id === selectedId)) {
      setSelectedId(astronauts[astronauts.length - 1].id)
    }
  }, [astronauts, selectedId])

  const selectAstronaut = useCallback((id: number) => {
    setSelectedId(id)
    writeLocal('am.astronaut', String(id))
  }, [])
  const setOperator = useCallback((name: string) => {
    setOperatorState(name)
    writeLocal('am.operator', name || null)
  }, [])

  const value = useMemo<SessionValue>(
    () => ({
      astronauts,
      astronautsLoading: isLoading,
      astronaut: astronauts.find((a) => a.id === selectedId) ?? null,
      selectAstronaut,
      operator,
      setOperator,
    }),
    [astronauts, isLoading, selectedId, selectAstronaut, operator, setOperator],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const v = useContext(SessionContext)
  if (!v) throw new Error('useSession outside SessionProvider')
  return v
}
