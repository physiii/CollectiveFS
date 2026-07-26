import { useEffect, useRef, useState } from 'react'
import { getSystemOverview } from './api'

const MAX_POINTS = 60

/** Rolling window of system snapshots that the charts read from. */
export function useSystemHistory(intervalMs = 5000) {
  const [history, setHistory] = useState([])
  const bufferRef = useRef([])
  const lastNetRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    bufferRef.current = []
    lastNetRef.current = null
    setHistory([])

    async function tick() {
      try {
        const overview = await getSystemOverview()
        if (cancelled) return
        const ts = Date.now()

        // Interface counters are cumulative; the chart wants a rate, so
        // difference against the previous sample. Only physical links count —
        // bridge traffic also crosses them and would be counted twice.
        const physical = (overview.network ?? []).filter((iface) => !iface.virtual)
        const rxTotal = physical.reduce((sum, iface) => sum + (iface.rx_bytes || 0), 0)
        const txTotal = physical.reduce((sum, iface) => sum + (iface.tx_bytes || 0), 0)
        const previous = lastNetRef.current
        const elapsed = previous ? Math.max((ts - previous.ts) / 1000, 0.001) : 0
        const rx = previous ? Math.max((rxTotal - previous.rx) / elapsed, 0) : 0
        const tx = previous ? Math.max((txTotal - previous.tx) / elapsed, 0) : 0
        lastNetRef.current = { ts, rx: rxTotal, tx: txTotal }

        const snapshot = { ...overview, _ts: ts, _rxRate: rx, _txRate: tx }
        bufferRef.current = [...bufferRef.current.slice(-(MAX_POINTS - 1)), snapshot]
        setHistory(bufferRef.current)
      } catch {
        // A failed poll just skips a point; the next tick recovers.
      }
    }

    // A chart needs two points to draw a line. At the steady cadence that means
    // staring at a placeholder for a full interval, so take the second sample
    // early and settle into the normal rhythm afterwards.
    let interval
    const warmup = setTimeout(() => {
      if (cancelled) return
      void tick()
      interval = setInterval(tick, intervalMs)
    }, 1200)

    void tick()
    return () => {
      cancelled = true
      clearTimeout(warmup)
      clearInterval(interval)
    }
  }, [intervalMs])

  return history
}
