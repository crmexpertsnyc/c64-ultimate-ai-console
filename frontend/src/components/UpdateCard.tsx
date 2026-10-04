import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, errorMessage, get, post } from '../services/api'
import { Card, Spinner } from './common'
import { useToast } from './Toasts'

/** ⬆ About & updates: this version, the last update check, "Update now" (Windows) or the command to run. */
export interface UpdateStatus {
  version: string; checkedAt: string | null; newer: boolean; latest: string | null; url: string | null; notes: string | null
  source: 'release' | 'git' | null; error: string | null; behind: number | null; repo: string; git: boolean; canApply: boolean; command: string
}

export function UpdateCard() {
  const toast = useToast()
  const [st, setSt] = useState<UpdateStatus | null>(null)
  const [repo, setRepo] = useState('')
  const [busy, setBusy] = useState('')
  useEffect(() => { get<UpdateStatus>('/api/system/update').then((s) => { setSt(s); setRepo(s.repo) }).catch(() => {}) }, [])

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what)
    try { await fn() } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy('') }
  }
  const check = () => run('check', async () => {
    if (repo !== st?.repo) await api.saveSettings({ UPDATE_REPO: repo.trim() } as never)
    const s = await post<UpdateStatus>('/api/system/update/check')
    setSt(s)
    toast(s.error ? s.error : s.newer ? `A newer version is available: ${s.latest}` : "You're up to date", s.error ? 'error' : 'ok')
  })
  const apply = () => run('apply', async () => {
    if (!window.confirm('Install the new version now? The console restarts by itself and is back in about a minute.')) return
    await post('/api/system/update/apply')
    toast('Updating… the page reconnects when the console is back', 'ok')
  })
  if (!st) return null
  return (
    <Card title="⬆ About & updates" className="update-card">
      <p>Version <b>{st.version}</b>
        {st.checkedAt && <span className="muted small"> · checked {new Date(st.checkedAt).toLocaleString()}</span>}</p>
      {st.newer && (
        <p className="update-new">🎉 <b>A newer version is available{st.latest ? `: ${st.latest}` : ''}.</b>
          {st.url && <> <a href={st.url} target="_blank" rel="noopener noreferrer">What's new ↗</a></>}</p>
      )}
      {st.error && <p className="muted small">⚠ {st.error}</p>}
      <label className="field"><span>Update from (GitHub project, owner/name) {st.git && <span className="muted small">— optional for git copies</span>}</span>
        <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/c64-ai-console" maxLength={120} /></label>
      <div className="row-actions">
        <button className="btn btn-sm" onClick={check} disabled={!!busy}>{busy === 'check' ? <Spinner /> : '🔍'} Check now</button>
        {st.newer && st.canApply && <button className="btn btn-primary btn-sm" onClick={apply} disabled={!!busy}>⬆ Update now</button>}
      </div>
      {st.newer && !st.canApply && <p className="small">To update, run on the console's computer: <code>{st.command}</code></p>}
      <p className="muted small">Checked daily (Settings → Sources & updates). Nothing is installed without you pressing Update now.
        Your settings, library and saves are kept.</p>
    </Card>
  )
}

/** A one-line note on Home when a newer version is out. */
export function UpdateNotice() {
  const [st, setSt] = useState<UpdateStatus | null>(null)
  useEffect(() => { get<UpdateStatus>('/api/system/update').then(setSt).catch(() => {}) }, [])
  if (!st?.newer) return null
  return <p className="update-notice">⬆ A new version of the console is available{st.latest ? ` (${st.latest})` : ''}. <Link to="/settings#updates-app">Update →</Link></p>
}
