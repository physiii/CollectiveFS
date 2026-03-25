import { useEffect, useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
const RECONNECT_DELAY_MS = 3_000

/**
 * Opens a WebSocket to /ws and:
 *  - Calls onConnected(true/false) when the connection state changes
 *  - Updates the React Query cache when a file status message arrives
 */
export function useWebSocket({ onConnected } = {}) {
  const qc  = useQueryClient()
  const ref = useRef(null)
  const timerRef = useRef(null)
  const mountedRef = useRef(true)

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    try {
      const ws = new WebSocket(WS_URL)
      ref.current = ws

      ws.onopen = () => {
        if (mountedRef.current) onConnected?.(true)
        // Clear any pending reconnect timer
        if (timerRef.current) {
          clearTimeout(timerRef.current)
          timerRef.current = null
        }
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'status' && msg.file_id) {
            // Optimistically update the file in the query cache
            qc.setQueryData(['files'], (prev) => {
              if (!prev) return prev
              return prev.map((f) =>
                f.id === msg.file_id
                  ? { ...f, status: msg.status }
                  : f,
              )
            })
            qc.setQueryData(['file', msg.file_id], (prev) => {
              if (!prev) return prev
              return { ...prev, status: msg.status }
            })
            // If complete or error, refresh authoritative data
            if (msg.status === 'complete' || msg.status === 'error') {
              qc.invalidateQueries({ queryKey: ['files'] })
              qc.invalidateQueries({ queryKey: ['file', msg.file_id] })
              qc.invalidateQueries({ queryKey: ['stats'] })
            }
          }
        } catch {
          // Non-JSON or heartbeat – ignore
        }
      }

      ws.onerror = () => {
        // Will trigger onclose immediately after
      }

      ws.onclose = () => {
        if (!mountedRef.current) return
        onConnected?.(false)
        // Schedule reconnect
        timerRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
      }
    } catch {
      onConnected?.(false)
      timerRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
    }
  }, [qc, onConnected])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
      if (ref.current) {
        ref.current.onclose = null // prevent reconnect on unmount
        ref.current.close()
      }
    }
  }, [connect])

  /** Send a raw message (fire-and-forget) */
  const send = useCallback((msg) => {
    if (ref.current?.readyState === WebSocket.OPEN) {
      ref.current.send(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }, [])

  return { send }
}
