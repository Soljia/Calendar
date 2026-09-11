import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { api, ApiError } from '../services/api'
import { formatDateTime, pluralize } from '../services/format'
import type { Meeting } from '../types'

export function HistoryPage() {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Meeting | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    try {
      setMeetings(await api.meetings({ limit: 200 }))
      setError(null)
    } catch {
      setError('Could not load meeting history.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function confirmDelete() {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await api.deleteMeeting(pendingDelete.id)
      setMeetings((current) => current?.filter((m) => m.id !== pendingDelete.id) ?? null)
      setPendingDelete(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the meeting.')
    } finally {
      setDeleting(false)
    }
  }

  if (error && !meetings) return <div className="notice alert">{error}</div>
  if (!meetings) return <div className="empty">Loading…</div>

  return (
    <div className="narrow stack">
      <div>
        <h2>Meeting history</h2>
        <p className="muted small" style={{ marginTop: 4 }}>
          Open a past meeting to review its stored breakout-room chat, or delete it along with every
          room and message it contains.
        </p>
      </div>

      {error ? <div className="notice alert">{error}</div> : null}

      {meetings.length === 0 ? (
        <div className="empty">No meetings recorded yet.</div>
      ) : (
        <div>
          {meetings.map((meeting) => (
            <article className="card meeting-card" key={meeting.id}>
              <div className="row">
                <Link className="grow" to={`/meetings/${meeting.id}`}>
                  <strong>{meeting.topic}</strong>
                </Link>
                {meeting.active ? <span className="badge ok">Active</span> : null}
                {meeting.simulated ? <span className="badge warn">simulated</span> : null}
                {meeting.alert_count > 0 ? <span className="badge alert">⚠ {meeting.alert_count}</span> : null}
              </div>

              <p className="muted small" style={{ margin: '6px 0 0' }}>
                {formatDateTime(meeting.start_time)} · {pluralize(meeting.room_count, 'room')} ·{' '}
                {pluralize(meeting.message_count, 'message')}
                {meeting.zoom_meeting_id ? ` · Zoom ID ${meeting.zoom_meeting_id}` : ''}
              </p>
              <p className="faint mono" style={{ margin: '2px 0 10px' }}>{meeting.meeting_uuid}</p>

              <div className="row">
                <Link className="button small" to={`/meetings/${meeting.id}`}>
                  Open monitor
                </Link>
                <button
                  type="button"
                  className="button small danger"
                  onClick={() => setPendingDelete(meeting)}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {pendingDelete ? (
        <ConfirmDialog
          title="Delete this meeting?"
          destructive
          busy={deleting}
          confirmLabel="Delete permanently"
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void confirmDelete()}
        >
          <p>
            <strong>{pendingDelete.topic}</strong> and all{' '}
            {pluralize(pendingDelete.message_count, 'stored message')} across{' '}
            {pluralize(pendingDelete.room_count, 'room')} will be permanently removed.
          </p>
          <p>This cannot be undone.</p>
        </ConfirmDialog>
      ) : null}
    </div>
  )
}
