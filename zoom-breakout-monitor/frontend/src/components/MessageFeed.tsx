import { formatClockSeconds } from '../services/format'
import type { ChatMessage } from '../types'

interface Props {
  messages: ChatMessage[]
  emptyLabel?: string
}

/** The combined "All Messages" stream: time, room, sender, text. */
export function MessageFeed({ messages, emptyLabel = 'No messages match these filters.' }: Props) {
  if (messages.length === 0) {
    return <div className="empty">{emptyLabel}</div>
  }

  return (
    <div className="feed">
      {messages.map((message) => (
        <div key={message.id} className={`feed-row${message.is_alert ? ' alert' : ''}`}>
          <span className="time">{formatClockSeconds(message.timestamp)}</span>
          <span className="room" title={message.room_name}>
            {message.room_name}
          </span>
          <span className="sender" title={message.sender_name}>
            {message.sender_name}
          </span>
          <span className="text">
            {message.message}
            {message.is_private ? <span className="badge warn" style={{ marginLeft: 8 }}>private</span> : null}
            {message.is_alert && message.matched_keywords.length > 0 ? (
              <span className="badge alert" style={{ marginLeft: 8 }}>
                ⚠ {message.matched_keywords.join(', ')}
              </span>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  )
}
