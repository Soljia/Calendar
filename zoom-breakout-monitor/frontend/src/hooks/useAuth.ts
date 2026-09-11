import { createContext, useContext } from 'react'
import type { SessionInfo } from '../types'

export interface AuthContextValue {
  session: SessionInfo | null
  loading: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  /** Called when any request comes back 401, so the UI drops to the login page. */
  invalidate: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
