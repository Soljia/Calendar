import type { ChatMessage, MessageFilters } from '../types'

export const EMPTY_FILTERS: MessageFilters = {
  query: '',
  roomId: null,
  sender: '',
  alertsOnly: false,
  since: null,
}

export function hasActiveFilters(filters: MessageFilters): boolean {
  return (
    filters.query.trim() !== '' ||
    filters.roomId !== null ||
    filters.sender.trim() !== '' ||
    filters.alertsOnly ||
    filters.since !== null
  )
}

/**
 * Client-side mirror of the server's message filtering.
 *
 * The base list comes from the API; this decides whether a message arriving
 * live over the WebSocket belongs in the currently displayed view, so the feed
 * stays live without refetching on every single message.
 */
export function matchesFilters(message: ChatMessage, filters: MessageFilters): boolean {
  if (filters.roomId && message.room_id !== filters.roomId) return false
  if (filters.alertsOnly && !message.is_alert) return false

  const sender = filters.sender.trim().toLowerCase()
  if (sender && !message.sender_name.toLowerCase().includes(sender)) return false

  const query = filters.query.trim().toLowerCase()
  if (
    query &&
    !message.message.toLowerCase().includes(query) &&
    !message.sender_name.toLowerCase().includes(query)
  ) {
    return false
  }

  if (filters.since && new Date(message.timestamp).getTime() < new Date(filters.since).getTime()) {
    return false
  }

  return true
}

/** Turn the relative "last N minutes" control into an absolute ISO instant. */
export function sinceFromMinutes(minutes: number | null): string | null {
  if (minutes === null) return null
  return new Date(Date.now() - minutes * 60000).toISOString()
}
