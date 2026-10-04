import { useCallback, useEffect, useState } from 'react'
import { Card, Empty, Modal, formatBytes } from '../components/common'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { Recording, Screenshot } from '../shared/types'

export function GalleryPage() {
  const toast = useToast()
  const { live } = useLive()
  const [tab, setTab] = useState<'shots' | 'videos'>('shots')
  const [shots, setShots] = useState<Screenshot[]>([])
  const [recs, setRecs] = useState<Recording[]>([])
  const [open, setOpen] = useState<Screenshot | null>(null)
  const [showAuto, setShowAuto] = useState(false)

  const load = useCallback(() => {
    api.screenshots().then(setShots).catch((e) => toast(errorMessage(e), 'error'))
    api.recordings().then(setRecs).catch(() => {})
  }, [toast])
  useEffect(() => { load() }, [load, live?.running])

  const remove = async (name: string) => {
    if (!window.confirm('Delete this screenshot?')) return
    try {
      await api.deleteScreenshot(name)
      setOpen(null)
      load()
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const removeRec = async (name: string) => {
    if (!window.confirm('Delete this recording?')) return
    try {
      await api.deleteRecording(name)
      load()
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const cover = async (s: Screenshot) => {
    if (!s.gameId) return
    try {
      await api.setCover(s.gameId, s.name)
      toast(`Cover art set for ${s.title}`, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const share = async (s: Screenshot) => {
    const url = `${location.origin}${s.url}`
    try {
      if (navigator.share) {
        const blob = await (await fetch(s.url)).blob()
        const file = new File([blob], s.name, { type: 'image/png' })
        await navigator.share({ files: [file], title: s.title ?? 'C64 screenshot' })
      } else {
        await navigator.clipboard.writeText(url)
        toast('Link copied', 'ok')
      }
    } catch {
      /* share sheet dismissed */
    }
  }

  const visible = shots.filter((s) => showAuto || !s.auto)
  const when = (t: number) => new Date(t * 1000).toLocaleString()

  return (
    <div className="page">
      <div className="page-head">
        <h1>Gallery</h1>
        <div className="seg" role="tablist">
          <button role="tab" aria-selected={tab === 'shots'} className={`seg-btn ${tab === 'shots' ? 'on' : ''}`} onClick={() => setTab('shots')}>
            📷 Screenshots ({visible.length})
          </button>
          <button role="tab" aria-selected={tab === 'videos'} className={`seg-btn ${tab === 'videos' ? 'on' : ''}`} onClick={() => setTab('videos')}>
            🎬 Recordings ({recs.length})
          </button>
        </div>
        {tab === 'shots' && (
          <label className="toggle"><input type="checkbox" checked={showAuto} onChange={(e) => setShowAuto(e.target.checked)} /> Show automatic cover shots</label>
        )}
      </div>

      {tab === 'shots' && (visible.length ? (
        <div className="shot-grid">
          {visible.map((s) => (
            <figure key={s.name} className="shot">
              <button className="shot-img" onClick={() => setOpen(s)} aria-label={`Open ${s.title ?? 'screenshot'}`}>
                <img src={s.url} alt={s.title ?? 'C64 screenshot'} loading="lazy" />
              </button>
              <figcaption>
                <strong>{s.title ?? 'C64'}</strong>
                <span className="muted small">{when(s.takenAt)}{s.auto ? ' · auto cover' : ''}</span>
              </figcaption>
            </figure>
          ))}
        </div>
      ) : <Card><Empty>No screenshots yet. On the Display page press 📷 (or S) while playing.</Empty></Card>)}

      {tab === 'videos' && (recs.length ? (
        <div className="rec-list">
          {recs.map((r) => (
            <Card key={r.name}>
              <video className="rec-video" src={r.url} controls preload="metadata" />
              <div className="row-actions">
                <span className="muted small">{when(r.createdAt)} · {formatBytes(r.size)}</span>
                <a className="btn btn-sm" href={`${r.url}?download=1`}>⬇ Download</a>
                <button className="btn btn-ghost btn-sm" onClick={() => removeRec(r.name)}>Delete</button>
              </div>
            </Card>
          ))}
        </div>
      ) : <Card><Empty>No recordings yet. On the Display page press ⏺ Record.</Empty></Card>)}

      <Modal open={!!open} title={open?.title ?? 'Screenshot'} onClose={() => setOpen(null)} footer={open && (
        <>
          {open.gameId && <button className="btn" onClick={() => cover(open)}>🖼 Use as cover</button>}
          <button className="btn" onClick={() => share(open)}>↗ Share</button>
          <a className="btn" href={`${open.url}?download=1`}>⬇ Download</a>
          <button className="btn btn-danger" onClick={() => remove(open.name)}>Delete</button>
        </>
      )}>
        {open && <img className="shot-full" src={open.url} alt={open.title ?? 'C64 screenshot'} />}
      </Modal>
    </div>
  )
}
