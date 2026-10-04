import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { LiveStatus } from '../shared/types'
import { useToast } from './Toasts'

function clock(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`
}

/** Go live (Twitch/YouTube/RTMP), record to MP4, and the OBS "stream view" link. */
export function LivePanel() {
  const { live } = useLive()
  const [st, setSt] = useState<LiveStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const toast = useToast()
  const navigate = useNavigate()

  useEffect(() => { api.live().then(setSt).catch(() => {}) }, [])
  useEffect(() => { if (live) setSt(live) }, [live])
  useEffect(() => {
    if (!st?.running) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [st?.running])

  const act = async (fn: () => Promise<LiveStatus>, ok: string) => {
    setBusy(true)
    try {
      setSt(await fn())
      toast(ok, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const obsUrl = `${location.origin}/stream-view`
  const copyObs = () => navigator.clipboard.writeText(obsUrl).then(() => toast('OBS link copied', 'ok'))
  const elapsed = st?.running && st.startedAt ? Math.max(0, Math.round(now / 1000 - st.startedAt)) : 0
  const liveReady = !!st?.rtmpUrl && !!st?.streamKeySet

  if (!st) return null
  return (
    <div className="live-panel">
      {st.running ? (
        <div className="live-on">
          <span className={`live-dot ${st.mode === 'live' ? 'red' : 'rec'}`} />
          <strong>{st.mode === 'live' ? 'LIVE' : 'REC'}</strong>
          <span className="mono">{clock(elapsed)}</span>
          <span className="muted small">
            {st.mode === 'live' ? st.target : st.file}
            {st.stats?.bitrate ? ` · ${st.stats.bitrate}` : ''}{st.stats?.speed ? ` · ${st.stats.speed}` : ''}
          </span>
          <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => act(api.liveStop, st.mode === 'live' ? 'Stream ended' : 'Recording saved')}>
            ■ Stop
          </button>
        </div>
      ) : (
        <div className="live-off">
          <button className="btn btn-sm live-go" disabled={busy || !st.ffmpeg || !liveReady}
            title={liveReady ? `Stream to ${st.rtmpUrl}` : 'Add your stream key in Settings → Streaming'}
            onClick={() => act(() => api.liveStart('live'), 'You are live!')}>● Go live</button>
          <button className="btn btn-sm" disabled={busy || !st.ffmpeg} onClick={() => act(() => api.liveStart('record'), 'Recording…')}>
            ⏺ Record
          </button>
          <button className="btn btn-ghost btn-sm" onClick={copyObs} title={obsUrl}>🔗 OBS link</button>
          {!liveReady && <button className="link small" onClick={() => navigate('/settings#streaming')}>Set up streaming</button>}
          {!st.ffmpeg && <span className="error-text small">ffmpeg not found</span>}
        </div>
      )}
      {st.error && !st.running && <div className="error-text small">Last session ended with an error: {st.error}</div>}
    </div>
  )
}
