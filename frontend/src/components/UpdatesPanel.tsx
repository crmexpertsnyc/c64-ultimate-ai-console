import { useCallback, useEffect, useState } from 'react'
import { get, post, request, errorMessage } from '../services/api'
import { useToast } from './Toasts'
import { Spinner } from './common'

interface UpdateJob {
  key: string; label: string; area: string; note: string; every: number; everyDefault: number; enabled: boolean
  blocked: string | null; running: boolean; queued: boolean; lastAt: string | null; lastOk: boolean | null
  lastSummary: string | null; lastError: string | null; lastSeconds: number | null; runs: number; nextAt: string | null
}
interface Updates { jobs: UpdateJob[]; choices: Record<string, number>; enabled: boolean; running: string | null }

const AREA: Record<string, string> = { news: '📰', releases: '🆕', events: '📅', magazines: '📚', hardware: '🛒', firmware: '🧩', system: '💾', bbs: '📟' }
const EVERY_LABEL: Record<string, string> = { '15m': 'every 15 min', '30m': 'every 30 min', '1h': 'hourly', '6h': 'every 6 hours',
  '12h': 'twice a day', '1d': 'daily', '7d': 'weekly', '30d': 'monthly' }

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}
function until(iso: string | null): string {
  if (!iso) return ''
  const s = (new Date(iso).getTime() - Date.now()) / 1000
  if (s <= 60) return 'due now'
  if (s < 3600) return `in ${Math.round(s / 60)} min`
  if (s < 86400) return `in ${Math.round(s / 3600)} h`
  return `in ${Math.round(s / 86400)} d`
}

/** 🔄 Every source the console keeps up to date by itself: when, how often, how it went — and "check now". */
export function UpdatesPanel() {
  const toast = useToast()
  const [u, setU] = useState<Updates | null>(null)
  const load = useCallback(() => get<Updates>('/api/updates').then(setU).catch(() => {}), [])
  useEffect(() => {
    load()
    const t = window.setInterval(load, 5000)
    return () => window.clearInterval(t)
  }, [load])
  const change = async (key: string, body: { every?: number; enabled?: boolean }) => {
    try { await request('PATCH', `/api/updates/${key}`, body); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  const run = async (j: UpdateJob) => {
    try { await post(`/api/updates/${j.key}/run`); toast(`🔄 Checking ${j.label}…`, 'ok'); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  if (!u) return <Spinner />
  return (
    <div className="updates">
      {!u.enabled && <p className="banner banner-warn small">Background updates are off — turn on “Check news, releases, events… in the background” above. “Check now” still works.</p>}
      <ul className="updates-list">
        {u.jobs.map((j) => (
          <li key={j.key} className={`${j.enabled ? '' : 'off'} ${j.lastOk === false ? 'failed' : ''}`}>
            <div className="updates-main">
              <strong>{AREA[j.area] ?? '🔄'} {j.label}</strong>
              <span className="muted small">{j.note}</span>
              <span className="small">
                {j.running ? <><Spinner /> checking now…</> : j.queued ? '⏳ queued' : <>Last checked {ago(j.lastAt)}
                  {j.lastOk === false ? <span className="error-text"> — failed: {j.lastError}</span> : j.lastSummary ? ` — ${j.lastSummary}` : ''}
                  {j.blocked ? <span className="muted"> · {j.blocked}</span> : j.enabled && j.nextAt ? <span className="muted"> · next {until(j.nextAt)}</span> : ''}</>}
              </span>
            </div>
            <div className="updates-ctl">
              <select value={j.every} disabled={!j.enabled} onChange={(e) => change(j.key, { every: Number(e.target.value) })} aria-label="How often">
                {Object.entries(u.choices).map(([k, v]) => <option key={k} value={v}>{EVERY_LABEL[k] ?? k}{v === j.everyDefault ? ' (default)' : ''}</option>)}
              </select>
              <label className="small"><input type="checkbox" checked={j.enabled} onChange={(e) => change(j.key, { enabled: e.target.checked })} /> On</label>
              <button className="btn btn-sm" disabled={j.running || j.queued || !!j.blocked} onClick={() => run(j)}>Check now</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
