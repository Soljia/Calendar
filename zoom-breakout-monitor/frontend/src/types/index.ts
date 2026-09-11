/** Mirrors the Pydantic models in backend/app/schemas/api.py. */

export interface SessionInfo {
  authenticated: boolean
  username: string | null
  expires_at: string | null
}

export interface ChatMessage {
  id: string
  meeting_uuid: string
  zoom_meeting_id: string | null
  room_id: string
  room_name: string
  room_number: number
  breakout_room_uuid: string | null
  sender_name: string
  sender_id: string | null
  message: string
  timestamp: string
  sender_context: string | null
  recipient_context: string | null
  recipient_type: string | null
  is_private: boolean
  is_alert: boolean
  matched_keywords: string[]
  simulated: boolean
}

export interface Room {
  id: string
  meeting_uuid: string
  breakout_room_uuid: string | null
  name: string
  room_number: number
  is_main_room: boolean
  custom_name: string | null
  first_seen: string
  last_activity: string
  message_count: number
  alert_count: number
  recent_messages: ChatMessage[]
}

export interface Meeting {
  id: string
  zoom_meeting_id: string | null
  meeting_uuid: string
  topic: string
  start_time: string
  last_activity: string
  active: boolean
  simulated: boolean
  room_count: number
  message_count: number
  alert_count: number
}

export interface MeetingDetail extends Meeting {
  rooms: Room[]
}

export interface MessagePage {
  items: ChatMessage[]
  total: number
  limit: number
  offset: number
}

export interface AppSettings {
  alerts_enabled: boolean
  alert_keywords: string[]
  browser_notifications_enabled: boolean
  inactivity_warning_minutes: number
  /** Read-only: controlled by DISPLAY_PRIVATE_MESSAGES in the environment. */
  display_private_messages: boolean
  /** Read-only: controlled by CHAT_RETENTION_DAYS in the environment. */
  chat_retention_days: number
  dev_mode: boolean
}

export interface DeleteResult {
  deleted: boolean
  meeting_uuid: string
  rooms_deleted: number
  messages_deleted: number
}

export interface SimulationStatus {
  running: boolean
  meeting_uuid: string | null
  topic: string | null
  rooms: number
  messages_sent: number
  messages_planned: number
}

export interface RoomNameSuggestions {
  available: boolean
  reason: string | null
  names: string[]
}

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

export type ServerEvent =
  | { type: 'hello'; data: { scope: string; user: string; meeting_uuid?: string } }
  | { type: 'chat_message'; data: { room: Room; message: ChatMessage } }
  | { type: 'room_created'; data: Room }
  | { type: 'room_updated'; data: Room }
  | { type: 'meeting_created'; data: Meeting }
  | { type: 'meeting_updated'; data: Meeting }
  | { type: 'meeting_deleted'; data: { meeting_uuid: string; id?: string } }
  | { type: 'settings_updated'; data: AppSettings }
  | { type: 'simulation_status'; data: SimulationStatus }
  | { type: 'pong'; data: Record<string, never> }

export interface MessageFilters {
  query: string
  roomId: string | null
  sender: string
  alertsOnly: boolean
  since: string | null
}
