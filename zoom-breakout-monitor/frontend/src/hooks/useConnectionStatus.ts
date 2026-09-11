import { createContext, useContext } from 'react'
import type { ConnectionState } from '../types'

interface ConnectionStatusValue {
  state: ConnectionState
  retry: (() => void) | null
  /** Pages with a live socket publish their status so the header can show it. */
  publish: (state: ConnectionState, retry: (() => void) | null) => void
}

export const ConnectionStatusContext = createContext<ConnectionStatusValue>({
  state: 'disconnected',
  retry: null,
  publish: () => {},
})

export function useConnectionStatus() {
  return useContext(ConnectionStatusContext)
}
