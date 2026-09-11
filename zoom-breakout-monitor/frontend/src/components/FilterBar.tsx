import type { MessageFilters, Room } from '../types'
import { sinceFromMinutes } from '../services/filters'

interface Props {
  filters: MessageFilters
  rooms: Room[]
  onChange: (filters: MessageFilters) => void
  onReset: () => void
  showRoomFilter?: boolean
}

const TIME_WINDOWS: Array<{ label: string; minutes: number | null }> = [
  { label: 'All time', minutes: null },
  { label: 'Last 5 min', minutes: 5 },
  { label: 'Last 15 min', minutes: 15 },
  { label: 'Last hour', minutes: 60 },
]

export function FilterBar({ filters, rooms, onChange, onReset, showRoomFilter = true }: Props) {
  return (
    <>
      <input
        className="input search"
        type="search"
        placeholder="Search messages and senders…"
        value={filters.query}
        aria-label="Search messages"
        onChange={(event) => onChange({ ...filters, query: event.target.value })}
      />

      <input
        className="input"
        style={{ width: 160 }}
        type="search"
        placeholder="Sender"
        value={filters.sender}
        aria-label="Filter by sender"
        onChange={(event) => onChange({ ...filters, sender: event.target.value })}
      />

      {showRoomFilter ? (
        <select
          className="select"
          style={{ width: 180 }}
          value={filters.roomId ?? ''}
          aria-label="Filter by room"
          onChange={(event) => onChange({ ...filters, roomId: event.target.value || null })}
        >
          <option value="">All rooms</option>
          {rooms.map((room) => (
            <option key={room.id} value={room.id}>
              {room.name}
            </option>
          ))}
        </select>
      ) : null}

      <select
        className="select"
        style={{ width: 140 }}
        aria-label="Filter by time"
        defaultValue=""
        onChange={(event) =>
          onChange({
            ...filters,
            since: sinceFromMinutes(event.target.value ? Number(event.target.value) : null),
          })
        }
      >
        {TIME_WINDOWS.map((window) => (
          <option key={window.label} value={window.minutes ?? ''}>
            {window.label}
          </option>
        ))}
      </select>

      <label className="row small" style={{ gap: 6 }}>
        <input
          type="checkbox"
          checked={filters.alertsOnly}
          onChange={(event) => onChange({ ...filters, alertsOnly: event.target.checked })}
        />
        Alerts only
      </label>

      <button type="button" className="button small ghost" onClick={onReset}>
        Clear
      </button>
    </>
  )
}
