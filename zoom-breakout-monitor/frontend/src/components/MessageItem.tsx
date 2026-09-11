import { formatClock } from '../services/format'
import type { ChatMessage } from '../types'

export function MessageItem({ message }: { message: ChatMessage }) {
  const classes = ['message']
  if (message.is_alert) classes.push('alert')
  if (message.is_private) classes.push('private')

  return (
    <div className={classes.join(' ')}>
      <div className="meta">
        <span className="sender">{message.sender_name}</span>
        <span className="time">{formatClock(message.timestamp)}</span>
        {message.is_private ? (
          <span className="badge warn" title="Direct message">
            private
          </span>
        ) : null}
        {message.is_alert && message.matched_keywords.length > 0 ? (
          <span className="badge alert" title={`Matched: ${message.matched_keywords.join(', ')}`}>
            ⚠ {message.matched_keywords[0]}
          </span>
        ) : null}
      </div>
      {/* React escapes this; message text is never rendered as HTML. */}
      <div className="body">{message.message}</div>
    </div>
  )
}
