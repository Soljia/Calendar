import { useCallback, useEffect, useState } from 'react'

type Permission = 'default' | 'granted' | 'denied' | 'unsupported'

/**
 * Browser notifications, permission-gated.
 *
 * Nothing is ever requested or shown unless the operator turned the setting on
 * *and* the browser granted permission after an explicit click. Message text is
 * deliberately not put in the notification body -- a chat line should not end
 * up on a lock screen.
 */
export function useNotifications(enabled: boolean) {
  const [permission, setPermission] = useState<Permission>(() =>
    typeof Notification === 'undefined' ? 'unsupported' : (Notification.permission as Permission),
  )

  useEffect(() => {
    if (typeof Notification === 'undefined') setPermission('unsupported')
  }, [])

  const requestPermission = useCallback(async () => {
    if (typeof Notification === 'undefined') return 'unsupported' as Permission
    const result = (await Notification.requestPermission()) as Permission
    setPermission(result)
    return result
  }, [])

  const notify = useCallback(
    (title: string, body: string) => {
      if (!enabled || permission !== 'granted' || typeof Notification === 'undefined') return
      try {
        new Notification(title, { body, tag: 'zbm-alert' })
      } catch {
        /* some browsers require a service worker; silently skip */
      }
    },
    [enabled, permission],
  )

  return { permission, requestPermission, notify, supported: permission !== 'unsupported' }
}
