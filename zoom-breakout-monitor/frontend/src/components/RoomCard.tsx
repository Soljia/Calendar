import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { MessageItem } from './MessageItem'
import { activityLabel, minutesSince, pluralize } from '../services/format'
import type { ChatMessage, Room } from '../types'

/** Distance from the bottom (px) still counted as "reading the newest". */
const STICK_THRESHOLD = 48

interface Props {
  room: Room
  messages: ChatMessage[]
  unread: number
  now: number
  inactivityWarningMinutes: number
  onRename: (room: Room) => void
  onSeen: (roomId: string) => void
}

export function RoomCard({
  room,
  messages,
  unread,
  now,
  inactivityWarningMinutes,
  onRename,
  onSeen,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinnedToBottom, setPinnedToBottom] = useState(true)

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const node = scrollRef.current
    if (!node) return
    node.scrollTo({ top: node.scrollHeight, behavior })
  }, [])

  // Start at the newest message without animating on first paint.
  useLayoutEffect(() => {
    scrollToBottom('auto')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.id])

  // Follow new messages only while the reader is already at the bottom.
  // Someone scrolled up is reading history, and must not be yanked away.
  useEffect(() => {
    if (pinnedToBottom) {
      scrollToBottom()
      if (unread > 0) onSeen(room.id)
    }
  }, [messages.length, pinnedToBottom, scrollToBottom, unread, onSeen, room.id])

  const handleScroll = useCallback(() => {
    const node = scrollRef.current
    if (!node) return
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight
    setPinnedToBottom(distance <= STICK_THRESHOLD)
  }, [])

  const quietMinutes = minutesSince(room.last_activity, now)
  const isQuiet = quietMinutes >= inactivityWarningMinutes
  const isActive = quietMinutes < 1
  const alertCount = room.alert_count

  const classes = ['room-card']
  if (alertCount > 0) classes.push('has-alert')
  if (unread > 0) classes.push('unread')

  return (
    <section className={classes.join(' ')} aria-label={room.name}>
      <header>
        <div className="room-title">
          <h3 title={room.name}>{room.name}</h3>
          {alertCount > 0 ? (
            <span className="badge alert" title={pluralize(alertCount, 'alert')}>
              ⚠ {alertCount}
            </span>
          ) : null}
          {unread > 0 ? <span className="badge accent">{unread} new</span> : null}
          <button
            type="button"
            className="button small ghost"
            onClick={() => onRename(room)}
            title="Rename this room"
            aria-label={`Rename ${room.name}`}
          >
            ✎
          </button>
        </div>
        <div className="room-meta">
          <span className={`badge ${isActive ? 'ok' : isQuiet ? 'warn' : ''}`}>
            {activityLabel(room.last_activity, now)}
          </span>
          <span className="faint">{pluralize(room.message_count, 'message')}</span>
          {room.is_main_room ? <span className="badge">main meeting</span> : null}
        </div>
      </header>

      <div className="room-messages" ref={scrollRef} onScroll={handleScroll}>
        {messages.length === 0 ? (
          <p className="faint small">No messages yet.</p>
        ) : (
          messages.map((message) => <MessageItem key={message.id} message={message} />)
        )}
      </div>

      {!pinnedToBottom ? (
        <button
          type="button"
          className="scroll-pin"
          onClick={() => {
            setPinnedToBottom(true)
            scrollToBottom()
            onSeen(room.id)
          }}
        >
          {unread > 0 ? `${unread} new message${unread === 1 ? '' : 's'} — jump to latest ↓` : 'Jump to latest ↓'}
        </button>
      ) : null}
    </section>
  )
}
