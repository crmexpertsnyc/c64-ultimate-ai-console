import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { CatalogItem } from '../shared/types'
import { useToast } from './Toasts'

export interface Alternative {
  kind: 'library' | 'catalog'
  title: string
  why: string
  gameId?: number
  format?: string
  hangs?: number
  catalog?: CatalogItem
}
export interface Alternatives { gameId: number; title: string; hangs: number; alternatives: Alternative[] }

/**
 * 🛟 Shown when a game seems stuck while loading (the screen has not changed for a while, nothing asked for a
 * key), or when this version got stuck before. Offers other versions that are likely to start.
 */
export function HangRescue({ gameId, before, onNudge, onDismiss, onLeave }: {
  gameId: number
  before: boolean            // true: "got stuck before" warning at load; false: stuck right now
  onNudge: () => void        // press Space / Fire, in case it is a quiet title screen
  onDismiss: () => void
  onLeave: () => void        // save nothing, leave cleanly before switching
}) {
  const navigate = useNavigate()
  const toast = useToast()
  const [alts, setAlts] = useState<Alternative[] | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.alternatives(gameId).then((r) => setAlts(r.alternatives)).catch(() => setAlts([])) }, [gameId])

  const tryIt = async (a: Alternative) => {
    setBusy(true)
    try {
      const id = a.gameId ?? (a.catalog ? (await api.catalogFetch(a.catalog)).gameId : null)
      if (!id) return
      onLeave()
      toast(`🛟 Trying ${a.title}`, 'ok')
      navigate(`/emulate/${id}`)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="emu-rescue" role="alert">
      <strong>{before ? '🛟 This version got stuck loading last time.' : '🛟 This seems stuck while loading.'}</strong>
      <span className="muted small">{before ? 'Another version usually starts fine in the browser:' : "Nothing has changed on screen for a while. Some releases' fast loaders don't work in the emulator."}</span>
      <div className="row-actions">
        {alts === null && <span className="muted small">Looking for other versions…</span>}
        {alts?.slice(0, 3).map((a) => (
          <button key={`${a.kind}:${a.gameId ?? a.catalog?.id}`} className="btn btn-primary btn-sm" disabled={busy} onClick={() => tryIt(a)} title={a.why}>
            ▶ Try {a.title}</button>
        ))}
        {alts !== null && alts.length === 0 && <span className="muted small">No other version found in your library or the catalog.</span>}
        {!before && <button className="btn btn-sm" onClick={onNudge} title="In case it is a title screen waiting for you">Press Space / Fire</button>}
        <button className="btn btn-ghost btn-sm" onClick={onDismiss}>{before ? 'Play this one anyway' : 'Keep waiting'}</button>
      </div>
    </div>
  )
}
