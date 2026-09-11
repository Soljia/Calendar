import { useEffect, useState } from 'react'
import type { Room, RoomNameSuggestions } from '../types'

interface Props {
  room: Room
  suggestions: RoomNameSuggestions | null
  busy?: boolean
  error?: string | null
  onSave: (name: string | null) => void
  onCancel: () => void
}

export function RenameRoomDialog({ room, suggestions, busy, error, onSave, onCancel }: Props) {
  const [value, setValue] = useState(room.custom_name ?? '')

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <form
        className="dialog card"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault()
          onSave(value.trim() || null)
        }}
      >
        <h2>Rename room</h2>
        <p className="muted small">
          Currently shown as <strong>{room.name}</strong>. Leave the field empty to restore the
          generated name.
        </p>

        <div className="field">
          <label htmlFor="room-name">Display name</label>
          <input
            id="room-name"
            className="input"
            value={value}
            maxLength={120}
            autoFocus
            placeholder={room.is_main_room ? 'Main Meeting' : `Breakout Room ${room.room_number}`}
            onChange={(event) => setValue(event.target.value)}
          />
          {room.breakout_room_uuid ? (
            <span className="hint mono">uuid {room.breakout_room_uuid}</span>
          ) : null}
        </div>

        {suggestions?.available && suggestions.names.length > 0 ? (
          <div className="field">
            <span className="hint">
              Breakout rooms pre-assigned on this Zoom meeting. Zoom does not tell us which UUID is
              which, so pick the one that matches.
            </span>
            <div className="keyword-list">
              {suggestions.names.map((name) => (
                <button
                  key={name}
                  type="button"
                  className="button small"
                  onClick={() => setValue(name)}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {error ? <div className="notice alert">{error}</div> : null}

        <div className="actions">
          <button type="button" className="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="button primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}
