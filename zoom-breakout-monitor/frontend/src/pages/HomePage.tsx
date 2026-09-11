import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { formatDateTime, pluralize } from '../services/format'
import { useWebSocket } from '../hooks/useWebSocket'
import { useConnectionStatus } from '../hooks/useConnectionStatus'
import type { Meeting, ServerEvent } from '../types'

export function HomePage() {
  const navigate = useNavigate()
  const { publish } = useConnectionStatus()
  const [meetings, setMeetings] = useState<Meeting[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setMeetings(await api.meetings({ limit: 25 }))
      setError(null)
    } catch {
      setError('Could not load meetings.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // The dashboard feed tells us when a brand-new meeting shows up, so the
  // home page does not need polling.
  const handleEvent = useCallback(
    (event: ServerEvent) => {
      if (event.type === 'meeting_created' || event.type === 'meeting_deleted') void load()
      if (event.type === 'chat_message') {
        setMeetings((current) =>
          current
            ? current.map((meeting) =>
                meeting.meeting_uuid === event.data.message.meeting_uuid
                  ? {
                      ...meeting,
                      message_count: meeting.message_count + 1,
                      alert_count: meeting.alert_count + (event.data.message.is_alert ? 1 : 0),
                      last_activity: event.data.message.timestamp,
                    }
                  : meeting,
              )
            : current,
        )
      }
    },
    [load],
  )

  const { state: connection, reconnectNow } = useWebSocket('/ws', handleEvent)

  useEffect(() => {
    publish(connection, reconnectNow)
  }, [connection, reconnectNow, publish])

  if (error) return <div className="notice alert">{error}</div>
  if (!meetings) return <div className="empty">Loading…</div>

  const [current, ...rest] = meetings

  return (
    <div className="narrow stack">
      <section>
        <h2 style={{ marginBottom: 10 }}>Current meeting</h2>
        {current ? (
          <article className="card meeting-card">
            <div className="row">
              <h3 className="grow">{current.topic}</h3>
              <span className={`badge ${current.active ? 'ok' : ''}`}>
                {current.active ? 'Active' : 'Ended'}
              </span>
              {current.simulated ? <span className="badge warn">simulated</span> : null}
            </div>
            <p className="muted small" style={{ marginTop: 4 }}>
              Started {formatDateTime(current.start_time)}
              {current.zoom_meeting_id ? ` · Zoom ID ${current.zoom_meeting_id}` : ''}
            </p>

            <div className="meeting-stats">
              <div className="stat">
                <span className="value">{current.room_count}</span>
                <span className="label">Rooms</span>
              </div>
              <div className="stat">
                <span className="value">{current.message_count}</span>
                <span className="label">Messages</span>
              </div>
              <div className="stat">
                <span className="value" style={current.alert_count ? { color: 'var(--alert)' } : undefined}>
                  {current.alert_count}
                </span>
                <span className="label">Alerts</span>
              </div>
            </div>

            <button type="button" className="button primary" onClick={() => navigate(`/meetings/${current.id}`)}>
              Open monitor
            </button>
          </article>
        ) : (
          <div className="card meeting-card">
            <p className="muted" style={{ margin: 0 }}>
              No meetings yet. Once Zoom delivers a <code>meeting.chat_message_sent</code> event the
              meeting will appear here automatically.
            </p>
          </div>
        )}
      </section>

      {rest.length > 0 ? (
        <section>
          <h2 style={{ marginBottom: 10 }}>Recent meetings</h2>
          {rest.map((meeting) => (
            <article className="card meeting-card" key={meeting.id}>
              <div className="row">
                <Link className="grow" to={`/meetings/${meeting.id}`}>
                  <strong>{meeting.topic}</strong>
                </Link>
                {meeting.alert_count > 0 ? <span className="badge alert">⚠ {meeting.alert_count}</span> : null}
              </div>
              <p className="muted small" style={{ margin: '4px 0 0' }}>
                {formatDateTime(meeting.start_time)} · {pluralize(meeting.room_count, 'room')} ·{' '}
                {pluralize(meeting.message_count, 'message')}
              </p>
            </article>
          ))}
          <p style={{ marginTop: 12 }}>
            <Link to="/history">See all meetings →</Link>
          </p>
        </section>
      ) : null}
    </div>
  )
}
