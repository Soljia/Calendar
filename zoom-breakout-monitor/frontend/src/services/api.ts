/**
 * REST client.
 *
 * Authentication rides on an HttpOnly cookie that the browser attaches
 * automatically, so there is no token to read, store or forward here. That is
 * deliberate: nothing in this bundle can see the session, which is what makes
 * an XSS bug unable to steal it.
 */

import type {
  AppSettings,
  DeleteResult,
  Meeting,
  MeetingDetail,
  MessagePage,
  Room,
  RoomNameSuggestions,
  SessionInfo,
  SimulationStatus,
} from '../types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: init.body ? { 'Content-Type': 'application/json', ...init.headers } : init.headers,
    ...init,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: never) => String((d as { msg?: string }).msg ?? d)).join(', ')
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  // --- auth ---
  session: () => request<SessionInfo>('/api/auth/session'),
  login: (username: string, password: string) =>
    request<SessionInfo>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<SessionInfo>('/api/auth/logout', { method: 'POST' }),

  // --- meetings ---
  meetings: (params: { active?: boolean; limit?: number } = {}) =>
    request<Meeting[]>(`/api/meetings${query(params)}`),
  currentMeeting: () => request<Meeting | null>('/api/meetings/current'),
  meeting: (id: string, includeMessages = true) =>
    request<MeetingDetail>(`/api/meetings/${encodeURIComponent(id)}${query({ include_messages: includeMessages })}`),
  rooms: (id: string, includeMessages = true) =>
    request<Room[]>(`/api/meetings/${encodeURIComponent(id)}/rooms${query({ include_messages: includeMessages })}`),
  messages: (
    id: string,
    params: {
      room_id?: string | null
      sender?: string
      q?: string
      alerts_only?: boolean
      since?: string | null
      until?: string | null
      limit?: number
      offset?: number
    } = {},
  ) => request<MessagePage>(`/api/meetings/${encodeURIComponent(id)}/messages${query(params)}`),
  roomNameSuggestions: (id: string) =>
    request<RoomNameSuggestions>(`/api/meetings/${encodeURIComponent(id)}/room-name-suggestions`),
  deleteMeeting: (id: string) =>
    request<DeleteResult>(`/api/meetings/${encodeURIComponent(id)}?confirm=true`, { method: 'DELETE' }),

  // --- rooms ---
  renameRoom: (roomId: string, customName: string | null) =>
    request<Room>(`/api/rooms/${encodeURIComponent(roomId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ custom_name: customName }),
    }),

  // --- settings ---
  settings: () => request<AppSettings>('/api/settings'),
  saveSettings: (settings: {
    alerts_enabled: boolean
    alert_keywords: string[]
    browser_notifications_enabled: boolean
    inactivity_warning_minutes: number
  }) => request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(settings) }),

  // --- development only ---
  simulationStatus: () => request<SimulationStatus>('/api/dev/simulation'),
  startSimulation: (payload: {
    topic: string
    rooms: number
    participants_per_room: number
    messages: number
    interval_seconds: number
  }) => request<SimulationStatus>('/api/dev/simulation/start', { method: 'POST', body: JSON.stringify(payload) }),
  stopSimulation: () => request<SimulationStatus>('/api/dev/simulation/stop', { method: 'POST' }),
}
