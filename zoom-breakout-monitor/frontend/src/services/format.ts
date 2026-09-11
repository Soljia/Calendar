/** Shared formatting helpers. All backend timestamps are UTC ISO-8601. */

export function parseTimestamp(value: string): Date {
  return new Date(value)
}

export function formatClock(value: string): string {
  return parseTimestamp(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function formatClockSeconds(value: string): string {
  return parseTimestamp(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatDateTime(value: string): string {
  return parseTimestamp(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function minutesSince(value: string, now: number): number {
  return Math.max(0, Math.floor((now - parseTimestamp(value).getTime()) / 60000))
}

/** "Active now" / "Quiet 3m" / "Quiet 2h 10m" */
export function activityLabel(lastActivity: string, now: number): string {
  const minutes = minutesSince(lastActivity, now)
  if (minutes < 1) return 'Active now'
  if (minutes < 60) return `Quiet ${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `Quiet ${hours}h ${rest}m` : `Quiet ${hours}h`
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}
