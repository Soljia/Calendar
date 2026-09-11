/**
 * Auto-reconnecting WebSocket with visible connection state.
 *
 * Reconnects with exponential backoff plus jitter, reconnects immediately when
 * the tab becomes visible again or the browser reports the network is back,
 * and sends a keepalive so an idle proxy does not silently drop the socket.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ConnectionState, ServerEvent } from '../types'

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 20000
const KEEPALIVE_MS = 25000

function socketUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export function useWebSocket(path: string | null, onEvent: (event: ServerEvent) => void) {
  const [state, setState] = useState<ConnectionState>(path ? 'connecting' : 'disconnected')
  const socketRef = useRef<WebSocket | null>(null)
  const attemptsRef = useRef(0)
  const reconnectTimer = useRef<number | null>(null)
  const keepaliveTimer = useRef<number | null>(null)
  const closedByUs = useRef(false)

  // Keep the latest callback without making it a reconnect trigger.
  const handlerRef = useRef(onEvent)
  useEffect(() => {
    handlerRef.current = onEvent
  }, [onEvent])

  const clearTimers = useCallback(() => {
    if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current)
    if (keepaliveTimer.current !== null) window.clearInterval(keepaliveTimer.current)
    reconnectTimer.current = null
    keepaliveTimer.current = null
  }, [])

  const connect = useCallback(() => {
    if (!path) return
    clearTimers()

    setState(attemptsRef.current === 0 ? 'connecting' : 'reconnecting')

    let socket: WebSocket
    try {
      socket = new WebSocket(socketUrl(path))
    } catch {
      scheduleReconnect()
      return
    }
    socketRef.current = socket

    socket.onopen = () => {
      attemptsRef.current = 0
      setState('connected')
      keepaliveTimer.current = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('ping')
      }, KEEPALIVE_MS)
    }

    socket.onmessage = (event) => {
      try {
        handlerRef.current(JSON.parse(event.data) as ServerEvent)
      } catch {
        /* ignore frames we cannot parse */
      }
    }

    socket.onclose = (event) => {
      clearTimers()
      socketRef.current = null
      if (closedByUs.current) {
        setState('disconnected')
        return
      }
      // 1008 is the policy-violation close the server sends when the session
      // is missing or expired. Reconnecting would loop, so stop and let the
      // app surface a sign-in prompt instead.
      if (event.code === 1008) {
        setState('disconnected')
        return
      }
      scheduleReconnect()
    }

    socket.onerror = () => {
      // onclose always follows; the reconnect is scheduled there.
    }

    function scheduleReconnect() {
      setState('reconnecting')
      const exponential = Math.min(INITIAL_BACKOFF_MS * 2 ** attemptsRef.current, MAX_BACKOFF_MS)
      const jitter = Math.random() * 0.3 * exponential
      attemptsRef.current += 1
      reconnectTimer.current = window.setTimeout(connect, exponential + jitter)
    }
  }, [path, clearTimers])

  useEffect(() => {
    closedByUs.current = false
    attemptsRef.current = 0
    if (!path) {
      setState('disconnected')
      return
    }
    connect()

    return () => {
      closedByUs.current = true
      clearTimers()
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [path, connect, clearTimers])

  // A laptop that was asleep comes back with a dead socket that never fires
  // onclose; nudge it when the tab or the network returns.
  useEffect(() => {
    const revive = () => {
      if (!path || closedByUs.current) return
      const socket = socketRef.current
      if (!socket || socket.readyState === WebSocket.CLOSED) {
        attemptsRef.current = 0
        connect()
      }
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') revive()
    }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('online', revive)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('online', revive)
    }
  }, [path, connect])

  const reconnectNow = useCallback(() => {
    attemptsRef.current = 0
    socketRef.current?.close()
    connect()
  }, [connect])

  return { state, reconnectNow }
}
