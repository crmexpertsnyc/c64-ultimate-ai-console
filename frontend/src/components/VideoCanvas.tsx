import { useEffect, useRef, useState } from 'react'

export interface VideoStats {
  srcFps: number          // frames/s arriving from the Ultimate
  displayFps: number      // frames/s actually drawn in this browser
  latencyAvg: number      // ms, frame complete on the console → drawn on screen
  latencyP95: number
  assemblyMs: number      // ms, first → last UDP packet of a frame (the C64 drawing the frame)
  encodeMs: number        // ms, JPEG encoding on the console
  deliveryMs: number      // ms, console send → browser receive
  drawMs: number          // ms, browser receive → drawn (decode + paint)
  skipped: number         // frames replaced by a newer one before they could be drawn
  clockRttMs: number      // ping round trip used for clock correction
  samples: number
}

interface Meta { n: number; complete: number; sent: number; assemblyMs: number; encodeMs: number; srcFps: number }

const WINDOW = 120

function avg(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
}

function p95(xs: number[]): number {
  if (!xs.length) return 0
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.floor(s.length * 0.95))]
}

/**
 * Draws the C64 picture from /ws/video on a canvas and measures, per frame, how long it took from
 * "frame complete on the console" to "drawn in this tab". Browser and server clocks are aligned with
 * ping/pong (offset from the lowest-RTT sample). Only the newest frame is ever decoded.
 */
export function VideoCanvas({ onStats, onError }: { onStats?: (s: VideoStats) => void; onError?: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) return
    ctx.imageSmoothingEnabled = false

    let offset = 0 // server clock − browser clock (ms)
    let bestRtt = Infinity
    let pending: { meta: Meta; blob: Blob; received: number } | null = null
    let busy = false
    let skipped = 0
    let closed = false
    const lat: number[] = []
    const del: number[] = []
    const draw: number[] = []
    const enc: number[] = []
    const asm: number[] = []
    const drawnAt: number[] = []
    let srcFps = 0

    const push = (arr: number[], v: number) => {
      arr.push(v)
      if (arr.length > WINDOW) arr.shift()
    }

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/video?fps=50`)
    ws.binaryType = 'arraybuffer'

    const ping = () => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping', t: Date.now() })) }
    const pingTimer = window.setInterval(ping, 2000)

    const render = async () => {
      if (busy || !pending || closed) return
      busy = true
      const job = pending
      pending = null
      try {
        const bmp = await createImageBitmap(job.blob)
        await new Promise<void>((resolve) => requestAnimationFrame(() => {
          if (canvas.width !== bmp.width) { canvas.width = bmp.width; canvas.height = bmp.height }
          ctx.drawImage(bmp, 0, 0)
          bmp.close()
          const now = Date.now()
          const nowServer = now + offset
          push(lat, nowServer - job.meta.complete)
          push(del, Math.max(0, job.received + offset - job.meta.sent))
          push(draw, now - job.received)
          push(enc, job.meta.encodeMs)
          push(asm, job.meta.assemblyMs)
          drawnAt.push(now)
          while (drawnAt.length && now - drawnAt[0] > 2000) drawnAt.shift()
          srcFps = job.meta.srcFps
          resolve()
        }))
        if (!ready) setReady(true)
      } catch {
        /* a corrupt frame is simply skipped */
      } finally {
        busy = false
        if (pending) render()
      }
    }

    ws.onopen = ping
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'pong') {
          const now = Date.now()
          const rtt = now - msg.t
          if (rtt <= bestRtt) {
            bestRtt = rtt
            offset = msg.server - (msg.t + rtt / 2)
          }
        }
        return
      }
      const buf = ev.data as ArrayBuffer
      const n = new DataView(buf).getUint32(0, true)
      const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n))) as Meta
      if (pending) skipped++
      pending = { meta, blob: new Blob([new Uint8Array(buf, 4 + n)], { type: 'image/jpeg' }), received: Date.now() }
      render()
    }
    ws.onerror = () => onError?.()

    const statsTimer = window.setInterval(() => {
      if (!onStats || !lat.length) return
      const span = drawnAt.length > 1 ? (drawnAt[drawnAt.length - 1] - drawnAt[0]) / 1000 : 0
      onStats({
        srcFps, displayFps: span > 0 ? (drawnAt.length - 1) / span : 0,
        latencyAvg: avg(lat), latencyP95: p95(lat), assemblyMs: avg(asm), encodeMs: avg(enc),
        deliveryMs: avg(del), drawMs: avg(draw), skipped, clockRttMs: bestRtt === Infinity ? 0 : bestRtt,
        samples: lat.length,
      })
    }, 500)

    return () => {
      closed = true
      window.clearInterval(pingTimer)
      window.clearInterval(statsTimer)
      ws.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="video-wrap">
      <canvas ref={canvasRef} className="video" width={768} height={544} aria-label="C64 display" />
      {!ready && <div className="video-wait muted small">Waiting for picture…</div>}
    </div>
  )
}

export function LatencyReadout({ stats }: { stats: VideoStats | null }) {
  if (!stats) return <div className="muted small">Measuring…</div>
  const f = (v: number) => v.toFixed(v < 10 ? 1 : 0)
  const tone = stats.latencyAvg < 50 ? 'ok' : stats.latencyAvg < 100 ? 'warn' : 'bad'
  return (
    <div className="latency">
      <div className="latency-main">
        <div className={`latency-big latency-${tone}`}>{f(stats.latencyAvg)}<span> ms</span></div>
        <div className="muted small">avg delay, frame ready → on screen · 95% under {f(stats.latencyP95)} ms</div>
      </div>
      <div className="latency-grid">
        <div><span className="muted">From C64</span><strong>{f(stats.srcFps)} fps</strong></div>
        <div><span className="muted">Displayed</span><strong>{f(stats.displayFps)} fps</strong></div>
        <div><span className="muted">Frame transfer</span><strong>{f(stats.assemblyMs)} ms</strong></div>
        <div><span className="muted">Encode</span><strong>{f(stats.encodeMs)} ms</strong></div>
        <div><span className="muted">Delivery</span><strong>{f(stats.deliveryMs)} ms</strong></div>
        <div><span className="muted">Decode + draw</span><strong>{f(stats.drawMs)} ms</strong></div>
        <div><span className="muted">Skipped (stale)</span><strong>{stats.skipped}</strong></div>
        <div><span className="muted">Clock sync RTT</span><strong>{f(stats.clockRttMs)} ms</strong></div>
      </div>
      <p className="muted small">
        Measured on {stats.samples} recent frames. “Frame transfer” is the C64 sending one frame (first to last
        packet); it happens before the delay above. Not included: the Ultimate’s internal capture and your
        monitor’s own refresh. Frame rate is capped at 50 fps (the C64’s PAL rate).
      </p>
    </div>
  )
}
