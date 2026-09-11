import type { ConnectionState } from '../types'

const LABELS: Record<ConnectionState, string> = {
  connected: 'Connected',
  connecting: 'Connecting',
  reconnecting: 'Reconnecting',
  disconnected: 'Disconnected',
}

interface Props {
  state: ConnectionState
  onRetry?: () => void
}

export function ConnectionBadge({ state, onRetry }: Props) {
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className={`connection ${state}`} role="status" aria-live="polite">
        <span className="dot" aria-hidden="true" />
        {LABELS[state]}
      </span>
      {state === 'disconnected' && onRetry ? (
        <button type="button" className="button small ghost" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </span>
  )
}
