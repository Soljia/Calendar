import { useState } from 'react'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../services/api'

export function LoginPage() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(username, password)
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? 'Too many attempts. Wait a minute and try again.'
          : 'Invalid username or password.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card card" onSubmit={handleSubmit}>
        <h1>Breakout Monitor</h1>
        <p className="muted small" style={{ marginBottom: 20 }}>
          Sign in to view breakout room chat.
        </p>

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            className="input"
            value={username}
            autoComplete="username"
            autoFocus
            required
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            value={password}
            autoComplete="current-password"
            required
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        {error ? <div className="notice alert">{error}</div> : null}

        <button type="submit" className="button primary" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }} disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
