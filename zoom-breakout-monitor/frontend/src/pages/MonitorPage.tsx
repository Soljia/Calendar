import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { RoomCard } from '../components/RoomCard'
import { MessageFeed } from '../components/MessageFeed'
import { FilterBar } from '../components/FilterBar'
import { RenameRoomDialog } from '../components/RenameRoomDialog'
import { useWebSocket } from '../hooks/useWebSocket'
import { useNow } from '../hooks/useNow'
import { useNotifications } from '../hooks/useNotifications'
import { useConnectionStatus } from '../hooks/useConnectionStatus'
import { api, ApiError } from '../services/api'
import { EMPTY_FILTERS, matchesFilters } from '../services/filters'
import { pluralize } from '../services/format'
import type {
  AppSettings,
  ChatMessage,
  MeetingDetail,
  MessageFilters,
  Room,
  RoomNameSuggestions,
  ServerEvent,
} from '../types'

type Tab = 'rooms' | 'all' | 'alerts'

/** Per-room in-memory cap, so a long class does not grow without bound. */
const MAX_ROOM_MESSAGES = 300
const MAX_FEED_MESSAGES = 1000

export function MonitorPage() {
  const { meetingId = '' } = useParams()
  const now = useNow()
  const { publish } = useConnectionStatus()

  const [meeting, setMeeting] = useState<MeetingDetail | null>(null)
  const [rooms, setRooms] = useState<Room[]>([])
  const [roomMessages, setRoomMessages] = useState<Record<string, ChatMessage[]>>({})
  const [unread, setUnread] = useState<Record<string, number>>({})
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('rooms')

  const [filters, setFilters] = useState<MessageFilters>(EMPTY_FILTERS)
  const [feed, setFeed] = useState<ChatMessage[]>([])
  const [feedTotal, setFeedTotal] = useState(0)
  const [feedLoading, setFeedLoading] = useState(false)

  const [renaming, setRenaming] = useState<Room | null>(null)
  const [renameBusy, setRenameBusy] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<RoomNameSuggestions | null>(null)

  const notifications = useNotifications(settings?.browser_notifications_enabled ?? false)
  const notifyRef = useRef(notifications.notify)
  useEffect(() => {
    notifyRef.current = notifications.notify
  }, [notifications.notify])

  const filtersRef = useRef<MessageFilters>(filters)
  const feedRequestId = useRef(0)

  // --- initial load ---------------------------------------------------

  useEffect(() => {
    let cancelled = false
    setMeeting(null)
    setError(null)

    void (async () => {
      try {
        const [detail, appSettings] = await Promise.all([api.meeting(meetingId), api.settings()])
        if (cancelled) return
        setMeeting(detail)
        setRooms(detail.rooms)
        setSettings(appSettings)
        setRoomMessages(
          Object.fromEntries(detail.rooms.map((room) => [room.id, room.recent_messages])),
        )
        setUnread({})
      } catch (err) {
        if (cancelled) return
        setError(
          err instanceof ApiError && err.status === 404
            ? 'That meeting no longer exists.'
            : 'Could not load this meeting.',
        )
      }
    })()

    return () => {
      cancelled = true
    }
  }, [meetingId])

  // --- combined feed --------------------------------------------------

  // The Alerts tab is the message filters plus an implied alerts-only. Folding
  // it in here keeps the server query and the live-append predicate in step.
  const effectiveFilters = useMemo<MessageFilters>(
    () => (tab === 'alerts' ? { ...filters, alertsOnly: true } : filters),
    [filters, tab],
  )

  useEffect(() => {
    filtersRef.current = effectiveFilters
  }, [effectiveFilters])

  const loadFeed = useCallback(
    async (activeFilters: MessageFilters) => {
      if (!meetingId) return
      const requestId = ++feedRequestId.current
      setFeedLoading(true)
      try {
        const page = await api.messages(meetingId, {
          q: activeFilters.query || undefined,
          sender: activeFilters.sender || undefined,
          room_id: activeFilters.roomId,
          alerts_only: activeFilters.alertsOnly,
          since: activeFilters.since,
          limit: 500,
        })
        // Responses can arrive out of order when filters change quickly; only
        // the newest request is allowed to replace what is on screen.
        if (requestId !== feedRequestId.current) return
        setFeed(page.items)
        setFeedTotal(page.total)
      } catch {
        if (requestId === feedRequestId.current) setError('Could not load the message feed.')
      } finally {
        if (requestId === feedRequestId.current) setFeedLoading(false)
      }
    },
    [meetingId],
  )

  // Debounced so typing in the search box does not fire a request per keystroke.
  useEffect(() => {
    if (tab === 'rooms') return
    const handle = window.setTimeout(() => void loadFeed(effectiveFilters), 300)
    return () => window.clearTimeout(handle)
  }, [tab, effectiveFilters, loadFeed])

  // --- live events ----------------------------------------------------

  const applyChatMessage = useCallback((message: ChatMessage, room: Room) => {
    setRooms((current) => {
      const index = current.findIndex((r) => r.id === room.id)
      if (index === -1) return [...current, room].sort((a, b) => a.room_number - b.room_number)
      const next = [...current]
      next[index] = room
      return next
    })

    setRoomMessages((current) => {
      const existing = current[message.room_id] ?? []
      if (existing.some((m) => m.id === message.id)) return current
      const appended = [...existing, message]
      return {
        ...current,
        [message.room_id]: appended.slice(-MAX_ROOM_MESSAGES),
      }
    })

    setUnread((current) => ({
      ...current,
      [message.room_id]: (current[message.room_id] ?? 0) + 1,
    }))

    // Keep the combined view live without refetching, using the same rules the
    // server applies.
    setFeed((current) => {
      if (current.some((m) => m.id === message.id)) return current
      if (!matchesFilters(message, filtersRef.current)) return current
      return [...current, message].slice(-MAX_FEED_MESSAGES)
    })
    setFeedTotal((total) => (matchesFilters(message, filtersRef.current) ? total + 1 : total))

    if (message.is_alert) {
      // No message text in the notification body: a chat line should not land
      // on a lock screen.
      notifyRef.current(
        `Alert in ${room.name}`,
        `${message.sender_name} triggered ${message.matched_keywords.join(', ') || 'an alert'}`,
      )
    }
  }, [])

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case 'chat_message':
          applyChatMessage(event.data.message, event.data.room)
          break
        case 'room_created':
          setRooms((current) =>
            current.some((room) => room.id === event.data.id)
              ? current
              : [...current, event.data].sort((a, b) => a.room_number - b.room_number),
          )
          setRoomMessages((current) => ({ ...current, [event.data.id]: [] }))
          break
        case 'room_updated':
          setRooms((current) =>
            current.map((room) => (room.id === event.data.id ? { ...room, ...event.data } : room)),
          )
          // Room names are denormalised onto messages for the combined view.
          setFeed((current) =>
            current.map((message) =>
              message.room_id === event.data.id ? { ...message, room_name: event.data.name } : message,
            ),
          )
          break
        case 'settings_updated':
          setSettings(event.data)
          break
        case 'meeting_deleted':
          setError('This meeting was deleted.')
          break
        default:
          break
      }
    },
    [applyChatMessage],
  )

  const { state: connection, reconnectNow } = useWebSocket(
    meeting ? `/ws/meetings/${encodeURIComponent(meetingId)}` : null,
    handleEvent,
  )

  useEffect(() => {
    publish(connection, reconnectNow)
  }, [connection, reconnectNow, publish])

  // --- room renaming ---------------------------------------------------

  const openRename = useCallback(
    (room: Room) => {
      setRenaming(room)
      setRenameError(null)
      if (suggestions === null) {
        void api
          .roomNameSuggestions(meetingId)
          .then(setSuggestions)
          .catch(() => setSuggestions({ available: false, reason: null, names: [] }))
      }
    },
    [meetingId, suggestions],
  )

  const saveRename = useCallback(
    async (name: string | null) => {
      if (!renaming) return
      setRenameBusy(true)
      setRenameError(null)
      try {
        const updated = await api.renameRoom(renaming.id, name)
        setRooms((current) => current.map((room) => (room.id === updated.id ? { ...room, ...updated } : room)))
        setRenaming(null)
      } catch (err) {
        setRenameError(err instanceof ApiError ? err.message : 'Could not rename the room.')
      } finally {
        setRenameBusy(false)
      }
    },
    [renaming],
  )

  const markSeen = useCallback((roomId: string) => {
    setUnread((current) => (current[roomId] ? { ...current, [roomId]: 0 } : current))
  }, [])

  // --- derived ---------------------------------------------------------

  const totalAlerts = useMemo(() => rooms.reduce((sum, room) => sum + room.alert_count, 0), [rooms])
  const totalMessages = useMemo(
    () => rooms.reduce((sum, room) => sum + room.message_count, 0),
    [rooms],
  )
  const quietRooms = useMemo(() => {
    const threshold = settings?.inactivity_warning_minutes ?? 5
    return rooms.filter(
      (room) => (now - new Date(room.last_activity).getTime()) / 60000 >= threshold,
    ).length
  }, [rooms, now, settings])

  const visibleRooms = useMemo(() => {
    if (!filters.roomId) return rooms
    return rooms.filter((room) => room.id === filters.roomId)
  }, [rooms, filters.roomId])

  if (error && !meeting) return <div className="notice alert">{error}</div>
  if (!meeting) return <div className="empty">Loading meeting…</div>

  return (
    <>
      {error ? <div className="notice alert" style={{ marginBottom: 14 }}>{error}</div> : null}

      {settings?.display_private_messages ? (
        <div className="notice warn" style={{ marginBottom: 14 }}>
          <strong>Private message monitoring is ON.</strong> Direct messages between participants
          are being stored and displayed. Set <code>DISPLAY_PRIVATE_MESSAGES=false</code> to turn
          this off.
        </div>
      ) : null}

      <div className="toolbar">
        <div className="row" role="tablist" aria-label="View">
          {(['rooms', 'all', 'alerts'] as Tab[]).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`button small ${tab === value ? 'primary' : 'ghost'}`}
              onClick={() => setTab(value)}
            >
              {value === 'rooms' ? 'Rooms' : value === 'all' ? 'All messages' : 'Alerts'}
            </button>
          ))}
        </div>

        <FilterBar
          filters={filters}
          rooms={rooms}
          onChange={setFilters}
          onReset={() => setFilters(EMPTY_FILTERS)}
        />

        <div className="counters">
          <span className={`badge ${totalAlerts ? 'alert' : ''}`}>Alerts: {totalAlerts}</span>
          <span className="badge">Rooms: {rooms.length}</span>
          <span className="badge">Messages: {totalMessages}</span>
          {quietRooms > 0 ? <span className="badge warn">Quiet: {quietRooms}</span> : null}
        </div>
      </div>

      {tab === 'rooms' ? (
        visibleRooms.length === 0 ? (
          <div className="empty">
            No breakout rooms yet. Rooms appear as soon as somebody sends a chat message in one.
          </div>
        ) : (
          <div className="room-grid">
            {visibleRooms.map((room) => (
              <RoomCard
                key={room.id}
                room={room}
                messages={roomMessages[room.id] ?? []}
                unread={unread[room.id] ?? 0}
                now={now}
                inactivityWarningMinutes={settings?.inactivity_warning_minutes ?? 5}
                onRename={openRename}
                onSeen={markSeen}
              />
            ))}
          </div>
        )
      ) : (
        <div className="card">
          <div className="row" style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
            <span className="muted small grow">
              {feedLoading ? 'Loading…' : `${pluralize(feedTotal, 'message')} matching`}
            </span>
            {notifications.supported && settings?.browser_notifications_enabled && notifications.permission === 'default' ? (
              <button type="button" className="button small" onClick={() => void notifications.requestPermission()}>
                Enable browser notifications
              </button>
            ) : null}
          </div>
          <MessageFeed
            messages={feed}
            emptyLabel={tab === 'alerts' ? 'No alerts yet.' : 'No messages match these filters.'}
          />
        </div>
      )}

      {renaming ? (
        <RenameRoomDialog
          room={renaming}
          suggestions={suggestions}
          busy={renameBusy}
          error={renameError}
          onSave={(name) => void saveRename(name)}
          onCancel={() => setRenaming(null)}
        />
      ) : null}
    </>
  )
}
