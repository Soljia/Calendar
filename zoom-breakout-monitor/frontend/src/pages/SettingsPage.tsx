import { useCallback, useEffect, useState } from 'react'
import { useNotifications } from '../hooks/useNotifications'
import { api, ApiError } from '../services/api'
import { pluralize } from '../services/format'
import type { AppSettings, SimulationStatus } from '../types'

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [keywordDraft, setKeywordDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const notifications = useNotifications(settings?.browser_notifications_enabled ?? false)

  useEffect(() => {
    void api
      .settings()
      .then(setSettings)
      .catch(() => setError('Could not load settings.'))
  }, [])

  const save = useCallback(async (next: AppSettings) => {
    setSaving(true)
    setError(null)
    setStatus(null)
    try {
      const saved = await api.saveSettings({
        alerts_enabled: next.alerts_enabled,
        alert_keywords: next.alert_keywords,
        browser_notifications_enabled: next.browser_notifications_enabled,
        inactivity_warning_minutes: next.inactivity_warning_minutes,
      })
      setSettings(saved)
      setStatus('Saved.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save settings.')
    } finally {
      setSaving(false)
    }
  }, [])

  if (!settings) {
    return error ? <div className="notice alert">{error}</div> : <div className="empty">Loading…</div>
  }

  function addKeyword() {
    if (!settings) return
    const keyword = keywordDraft.trim().toLowerCase()
    if (!keyword || settings.alert_keywords.includes(keyword)) {
      setKeywordDraft('')
      return
    }
    setSettings({ ...settings, alert_keywords: [...settings.alert_keywords, keyword] })
    setKeywordDraft('')
  }

  function removeKeyword(keyword: string) {
    if (!settings) return
    setSettings({
      ...settings,
      alert_keywords: settings.alert_keywords.filter((k) => k !== keyword),
    })
  }

  async function toggleNotifications(enabled: boolean) {
    if (!settings) return
    if (enabled && notifications.permission !== 'granted') {
      // Never enable notifications without an explicit browser grant.
      const result = await notifications.requestPermission()
      if (result !== 'granted') {
        setError('The browser did not grant notification permission, so this stays off.')
        setSettings({ ...settings, browser_notifications_enabled: false })
        return
      }
    }
    setSettings({ ...settings, browser_notifications_enabled: enabled })
  }

  return (
    <div className="narrow stack">
      <div>
        <h2>Settings</h2>
        <p className="muted small" style={{ marginTop: 4 }}>
          Alerting and activity thresholds are stored in the database and apply to every dashboard.
        </p>
      </div>

      {error ? <div className="notice alert">{error}</div> : null}
      {status ? <div className="notice accent">{status}</div> : null}

      <section className="card settings-section">
        <h2>Keyword alerts</h2>
        <p className="muted small">
          Matching is case-insensitive and anchored at a word start, so “help” matches “Help!” and
          “helping” but not “whelp”.
        </p>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.alerts_enabled}
            onChange={(event) => setSettings({ ...settings, alerts_enabled: event.target.checked })}
          />
          <span>
            <strong>Enable keyword alerts</strong>
            <br />
            <span className="muted small">
              When off, no message is flagged and alert counts stop increasing.
            </span>
          </span>
        </label>

        <div className="field">
          <label htmlFor="keyword">Keywords ({settings.alert_keywords.length})</label>
          <div className="keyword-list">
            {settings.alert_keywords.length === 0 ? (
              <span className="muted small">No keywords configured.</span>
            ) : (
              settings.alert_keywords.map((keyword) => (
                <span className="keyword" key={keyword}>
                  {keyword}
                  <button type="button" aria-label={`Remove ${keyword}`} onClick={() => removeKeyword(keyword)}>
                    ×
                  </button>
                </span>
              ))
            )}
          </div>
          <div className="row">
            <input
              id="keyword"
              className="input grow"
              value={keywordDraft}
              maxLength={64}
              placeholder="Add a keyword…"
              onChange={(event) => setKeywordDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  addKeyword()
                }
              }}
            />
            <button type="button" className="button" onClick={addKeyword}>
              Add
            </button>
          </div>
        </div>
      </section>

      <section className="card settings-section">
        <h2>Browser notifications</h2>
        <p className="muted small">
          Optional desktop notifications when a message triggers an alert. Nothing is ever shown
          until you grant permission, and the message text is deliberately left out of the
          notification body.
        </p>

        <label className="checkbox">
          <input
            type="checkbox"
            disabled={!notifications.supported}
            checked={settings.browser_notifications_enabled}
            onChange={(event) => void toggleNotifications(event.target.checked)}
          />
          <span>
            <strong>Show a notification for alert messages</strong>
            <br />
            <span className="muted small">
              {!notifications.supported
                ? 'This browser does not support notifications.'
                : notifications.permission === 'granted'
                  ? 'Permission granted.'
                  : notifications.permission === 'denied'
                    ? 'Permission was denied in the browser; re-allow it in site settings first.'
                    : 'You will be asked for permission when you turn this on.'}
            </span>
          </span>
        </label>
      </section>

      <section className="card settings-section">
        <h2>Room activity</h2>
        <p className="muted small">
          How long a room may stay silent before it is flagged as quiet, so you can spot a group
          that has stopped participating.
        </p>
        <div className="field" style={{ maxWidth: 260 }}>
          <label htmlFor="inactivity">Warn after (minutes)</label>
          <input
            id="inactivity"
            className="input"
            type="number"
            min={1}
            max={240}
            value={settings.inactivity_warning_minutes}
            onChange={(event) =>
              setSettings({
                ...settings,
                inactivity_warning_minutes: Number(event.target.value) || 1,
              })
            }
          />
        </div>
      </section>

      <section className="card settings-section">
        <h2>Privacy and retention</h2>
        <p className="muted small">
          These are set in the server environment, not here. Changing privacy behaviour from a
          browser session is deliberately not possible.
        </p>
        <div className="readonly-grid">
          <div className="item">
            <div className="k">Private messages</div>
            <div className="v" style={settings.display_private_messages ? { color: 'var(--warn)' } : undefined}>
              {settings.display_private_messages ? 'Stored and displayed' : 'Not stored, not shown'}
            </div>
            <div className="muted small" style={{ marginTop: 4 }}>
              <code>DISPLAY_PRIVATE_MESSAGES</code>
            </div>
          </div>
          <div className="item">
            <div className="k">Chat retention</div>
            <div className="v">
              {settings.chat_retention_days === 0
                ? 'Kept indefinitely'
                : pluralize(settings.chat_retention_days, 'day')}
            </div>
            <div className="muted small" style={{ marginTop: 4 }}>
              <code>CHAT_RETENTION_DAYS</code>
            </div>
          </div>
          <div className="item">
            <div className="k">Developer mode</div>
            <div className="v" style={settings.dev_mode ? { color: 'var(--warn)' } : undefined}>
              {settings.dev_mode ? 'On' : 'Off'}
            </div>
            <div className="muted small" style={{ marginTop: 4 }}>
              <code>DEV_MODE</code>
            </div>
          </div>
        </div>

        {settings.display_private_messages ? (
          <div className="notice warn" style={{ marginTop: 14 }}>
            Private message monitoring is enabled. Direct messages between participants are being
            recorded and shown to anyone who can sign in to this dashboard.
          </div>
        ) : null}
      </section>

      {settings.dev_mode ? <SimulationPanel /> : null}

      <div className="row">
        <button type="button" className="button primary" disabled={saving} onClick={() => void save(settings)}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </div>
    </div>
  )
}

/** Only rendered when the server reports DEV_MODE=true. */
function SimulationPanel() {
  const [status, setStatus] = useState<SimulationStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    topic: 'Simulated Class',
    rooms: 4,
    participants_per_room: 3,
    messages: 40,
    interval_seconds: 2,
  })

  useEffect(() => {
    void api.simulationStatus().then(setStatus).catch(() => setStatus(null))
  }, [])

  async function start() {
    setBusy(true)
    setError(null)
    try {
      setStatus(await api.startSimulation(form))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the simulation.')
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    try {
      setStatus(await api.stopSimulation())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not stop the simulation.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card settings-section">
      <h2>Simulation (developer mode)</h2>
      <p className="muted small">
        Generates fake breakout-room chat through the real ingestion path so the dashboard can be
        exercised without a live Zoom meeting. These controls do not exist when{' '}
        <code>DEV_MODE=false</code>.
      </p>

      {error ? <div className="notice alert">{error}</div> : null}

      <div className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
        <div className="field" style={{ marginBottom: 0, flex: '1 1 200px' }}>
          <label htmlFor="sim-topic">Topic</label>
          <input
            id="sim-topic"
            className="input"
            value={form.topic}
            onChange={(event) => setForm({ ...form, topic: event.target.value })}
          />
        </div>
        <div className="field" style={{ marginBottom: 0, width: 110 }}>
          <label htmlFor="sim-rooms">Rooms</label>
          <input
            id="sim-rooms"
            className="input"
            type="number"
            min={1}
            max={50}
            value={form.rooms}
            onChange={(event) => setForm({ ...form, rooms: Number(event.target.value) || 1 })}
          />
        </div>
        <div className="field" style={{ marginBottom: 0, width: 130 }}>
          <label htmlFor="sim-messages">Messages</label>
          <input
            id="sim-messages"
            className="input"
            type="number"
            min={0}
            max={500}
            value={form.messages}
            onChange={(event) => setForm({ ...form, messages: Number(event.target.value) || 0 })}
          />
        </div>
        <div className="field" style={{ marginBottom: 0, width: 130 }}>
          <label htmlFor="sim-interval">Interval (s)</label>
          <input
            id="sim-interval"
            className="input"
            type="number"
            min={0.1}
            max={60}
            step={0.1}
            value={form.interval_seconds}
            onChange={(event) =>
              setForm({ ...form, interval_seconds: Number(event.target.value) || 1 })
            }
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <button type="button" className="button primary" disabled={busy || status?.running} onClick={() => void start()}>
          Start simulation
        </button>
        <button type="button" className="button" disabled={busy || !status?.running} onClick={() => void stop()}>
          Stop
        </button>
        {status?.running ? (
          <span className="badge ok">
            running · {status.messages_sent} sent · {status.rooms} rooms
          </span>
        ) : (
          <span className="badge">idle</span>
        )}
      </div>
    </section>
  )
}
