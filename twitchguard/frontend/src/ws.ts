import { useEffect, useRef } from 'react'
import type { StreamEvent } from './types'

/** Live channel stream (IR-17) with auto-reconnect. */
export function useChannelStream(
  channelId: number | null | undefined,
  onEvent: (event: StreamEvent) => void,
): void {
  const callbackRef = useRef(onEvent)
  callbackRef.current = onEvent

  useEffect(() => {
    if (!channelId) return
    let socket: WebSocket | null = null
    let closed = false
    let retryTimer: number | undefined

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${proto}://${window.location.host}/channels/${channelId}/stream`)
      socket.onmessage = (msg) => {
        try {
          callbackRef.current(JSON.parse(msg.data as string) as StreamEvent)
        } catch {
          // ignore malformed frames
        }
      }
      socket.onclose = () => {
        if (!closed) retryTimer = window.setTimeout(connect, 2000)
      }
    }
    connect()
    return () => {
      closed = true
      if (retryTimer) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [channelId])
}
