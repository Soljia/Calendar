import { useCallback, useEffect, useMemo, useState } from 'react'
import { AuthContext } from '../hooks/useAuth'
import { api } from '../services/api'
import type { SessionInfo } from '../types'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    void api
      .session()
      .then(setSession)
      .catch(() => setSession({ authenticated: false, username: null, expires_at: null }))
      .finally(() => setLoading(false))
  }, [])

  const signIn = useCallback(async (username: string, password: string) => {
    setSession(await api.login(username, password))
  }, [])

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setSession({ authenticated: false, username: null, expires_at: null })
    }
  }, [])

  const invalidate = useCallback(() => {
    setSession({ authenticated: false, username: null, expires_at: null })
  }, [])

  const value = useMemo(
    () => ({ session, loading, signIn, signOut, invalidate }),
    [session, loading, signIn, signOut, invalidate],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
