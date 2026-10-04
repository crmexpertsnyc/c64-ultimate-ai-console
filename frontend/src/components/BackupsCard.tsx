import { useCallback, useEffect, useState } from 'react'
import { errorMessage, get, post } from '../services/api'
import { Card } from './common'
import { useToast } from './Toasts'

interface Backups { folder: string; keep: number; totalMB: number; backups: { name: string; sizeMB: number; full: boolean; at: string }[] }

/** 💾 Nightly backups of your data (library, saves, imports, screenshots, art, settings) — and "back up now". */
export function BackupsCard({ dir, keep, onDir, onKeep }: {
  dir: string; keep: number; onDir: (v: string) => void; onKeep: (v: number) => void
}) {
  const toast = useToast()
  const [b, setB] = useState<Backups | null>(null)
  const load = useCallback(() => get<Backups>('/api/backups').then(setB).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  const now = async () => {
    try {
      await post('/api/backups')
      toast('💾 Backing up — it takes a few seconds', 'ok')
      window.setTimeout(load, 8000)
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  return (
    <Card title="💾 Backups" actions={<button className="btn btn-sm" onClick={now}>Back up now</button>}>
      <p className="muted small">Every night the console saves your library, Browser Play saves, imported games, screenshots, recordings, box art and
        settings to a folder (Sundays also the magazine search index). Covers and catalog downloads aren't included — they come back by themselves.
        The backup includes your API keys, so keep the folder private. To restore: stop the console and unzip a backup into <code>backend/data</code>.</p>
      <div className="form-grid">
        <label className="field field-wide"><span>Backup folder</span>
          <input value={dir} onChange={(e) => onDir(e.target.value)} placeholder={b?.folder ?? 'Documents\\C64 Console Backups'} /></label>
        <label className="field"><span>Daily backups to keep</span>
          <input type="number" min={1} max={365} value={keep} onChange={(e) => onKeep(Number(e.target.value) || 14)} /></label>
      </div>
      {b && (
        <p className="small">{b.backups.length ? <>
          {b.backups.length} backup{b.backups.length === 1 ? '' : 's'} ({b.totalMB} MB) in <code>{b.folder}</code> · latest {b.backups[0].at.replace('T', ' ')}
          {b.backups[0].full ? ' (full)' : ''} — {b.backups[0].sizeMB} MB</> : <>No backups yet — the first runs tonight, or press “Back up now”. Folder: <code>{b.folder}</code></>}</p>
      )}
    </Card>
  )
}
