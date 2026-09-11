import { useCallback, useMemo, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider } from './components/AuthProvider'
import { ConnectionBadge } from './components/ConnectionBadge'
import { ConnectionStatusContext } from './hooks/useConnectionStatus'
import { useAuth } from './hooks/useAuth'
import { useTheme } from './hooks/useTheme'
import { HomePage } from './pages/HomePage'
import { HistoryPage } from './pages/HistoryPage'
import { LoginPage } from './pages/LoginPage'
import { MonitorPage } from './pages/MonitorPage'
import { SettingsPage } from './pages/SettingsPage'
import type { ConnectionState } from './types'

function MonitorRouteTitle() {
  // The monitor page owns the meeting data; the header only needs to know it
  // is the active route so the nav highlights correctly.
  const { meetingId } = useParams()
  return meetingId ? null : <Navigate to="/" replace />
}

function Shell() {
  const { session, loading, signOut } = useAuth()
  const { theme, toggle } = useTheme()
  const [connection, setConnection] = useState<{
    state: ConnectionState
    retry: (() => void) | null
  }>({ state: 'disconnected', retry: null })

  const publish = useCallback((state: ConnectionState, retry: (() => void) | null) => {
    setConnection((current) =>
      current.state === state && current.retry === retry ? current : { state, retry },
    )
  }, [])

  const connectionValue = useMemo(
    () => ({ state: connection.state, retry: connection.retry, publish }),
    [connection, publish],
  )

  if (loading) {
    return <div className="empty">Loading…</div>
  }

  if (!session?.authenticated) {
    return <LoginPage />
  }

  return (
    <ConnectionStatusContext.Provider value={connectionValue}>
      <div className="app">
        <header className="app-header">
          <div className="brand">
            <h1>Breakout Monitor</h1>
          </div>
          <div className="spacer" />
          <div className="header-actions">
            <ConnectionBadge state={connection.state} onRetry={connection.retry ?? undefined} />
            <button
              type="button"
              className="button small ghost"
              onClick={toggle}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            <span className="muted small">{session.username}</span>
            <button type="button" className="button small" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </header>

        <nav className="app-nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/history">Meeting history</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>

        <main className="app-main">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/meetings/:meetingId" element={<MonitorPage />} />
            <Route path="/meetings" element={<MonitorRouteTitle />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </ConnectionStatusContext.Provider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
