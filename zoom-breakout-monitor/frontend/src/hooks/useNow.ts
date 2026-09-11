import { useEffect, useState } from 'react'

/** A clock that ticks, so "quiet for N minutes" labels stay truthful. */
export function useNow(intervalMs = 15000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}
