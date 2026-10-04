import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, errorMessage } from '../services/api'
import { useToast } from './Toasts'
import { VideoCanvas } from './VideoCanvas'
import type { VideoStats } from './VideoCanvas'

/**
 * The C64 picture with "TV mode": double-click or ⛶ for fullscreen; controls fade out after a few
 * seconds without mouse/touch activity. 📷 saves a pixel-perfect PNG (captured on the server from
 * the raw C64 frame). Press S for a screenshot and F for fullscreen while the picture has focus.
 */
export function DisplaySurface({ videoKey, onStats, fallback, onFallback, gameId, title, extraControls }: {
  videoKey: number
  onStats?: (s: VideoStats) => void
  fallback: boolean
  onFallback: () => void
  gameId?: number | null
  title?: string | null
  extraControls?: ReactNode
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [full, setFull] = useState(false)
  const [idle, setIdle] = useState(false)
  const [flash, setFlash] = useState(false)
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const idleTimer = useRef<number | undefined>(undefined)

  const wake = useCallback(() => {
    setIdle(false)
    window.clearTimeout(idleTimer.current)
    idleTimer.current = window.setTimeout(() => setIdle(true), 2500)
  }, [])

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === wrapRef.current)
    document.addEventListener('fullscreenchange', onChange)
    wake()
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      window.clearTimeout(idleTimer.current)
    }
  }, [wake])

  const toggleFull = async () => {
    const el = wrapRef.current
    if (!el) return
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await el.requestFullscreen({ navigationUI: 'hide' })
    } catch {
      toast('Fullscreen is not available in this browser', 'error')
    }
  }

  const shoot = async () => {
    if (busy) return
    setBusy(true)
    setFlash(true)
    window.setTimeout(() => setFlash(false), 180)
    try {
      await api.screenshot(title ?? undefined, gameId ?? undefined)
      toast('Screenshot saved', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); shoot() }
    if (e.key === 'f' || e.key === 'F') { e.preventDefault(); toggleFull() }
  }

  return (
    <div
      ref={wrapRef}
      className={`display-surface ${full ? 'is-full' : ''} ${idle ? 'is-idle' : ''}`}
      onMouseMove={wake}
      onTouchStart={wake}
      onDoubleClick={toggleFull}
      onKeyDown={onKey}
      tabIndex={0}
      aria-label="C64 display. Press S for a screenshot, F for fullscreen."
    >
      {fallback
        ? <img className="video" src={`/api/streams/video.mjpeg?t=${videoKey}`} alt="C64 display" />
        : <VideoCanvas key={videoKey} onStats={onStats} onError={onFallback} />}
      {flash && <div className="shutter" />}
      <div className="surface-controls" onDoubleClick={(e) => e.stopPropagation()}>
        {extraControls}
        <button className="surface-btn" onClick={shoot} disabled={busy} title="Screenshot (S)" aria-label="Take screenshot">📷</button>
        <button className="surface-btn" onClick={toggleFull} title={full ? 'Exit TV mode (Esc)' : 'TV mode (F)'}
          aria-label={full ? 'Exit fullscreen' : 'Fullscreen'}>{full ? '🗗' : '⛶'}</button>
      </div>
    </div>
  )
}
