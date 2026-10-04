import { useEffect, useState } from 'react'
import { GearSuggestions } from '../components/GearSuggestions'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'

export interface Issue {
  id: number
  gameId: number | null
  title: string
  fileName: string | null
  sha256: string | null
  source: 'auto' | 'user'
  category: string
  categoryLabel: string
  note: string | null
  status: 'open' | 'investigating' | 'fixed' | 'wontfix'
  resolution: string | null
  occurrences: number
  createdAt: string | null
  lastSeenAt: string | null
  workedAt: string | null
  diagnostics: Record<string, unknown>
  analysis: { summary: string; likelyCause: string; confidence: string; steps: string[]; model: string | null } | null
  hasScreenshot: boolean
  coverUrl: string | null
  likelyCauses: string[]
}

const STATUS_LABEL: Record<Issue['status'], string> = { open: 'Open', investigating: 'Investigating', fixed: 'Fixed', wontfix: "Won't fix" }
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '')

/** 🐞 Compatibility log: games that did not work in Browser Play, with the evidence to investigate. */
export function CompatPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const filter = params.get('status') ?? 'active'
  const gameId = params.get('game') ? Number(params.get('game')) : undefined
  const [issues, setIssues] = useState<Issue[] | null>(null)

  const load = () => api.issues(filter === 'all' ? undefined : filter, gameId).then((r) => setIssues(r.issues)).catch((e) => toast(errorMessage(e), 'error'))
  useEffect(() => { load() }, [filter, gameId]) // eslint-disable-line react-hooks/exhaustive-deps

  const replace = (i: Issue) => setIssues((list) => list?.map((x) => (x.id === i.id ? i : x)) ?? null)
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>🐞 Compatibility log</h1>
        <div className="seg" role="group" aria-label="Filter">
          {[['active', 'Open'], ['fixed', 'Fixed'], ['all', 'All']].map(([v, l]) => (
            <button key={v} className={`seg-btn ${filter === v ? 'on' : ''}`} onClick={() => set('status', v === 'active' ? null : v)}>{l}</button>
          ))}
        </div>
        {gameId && <button className="btn btn-ghost btn-sm" onClick={() => set('game', null)}>✕ Only this game</button>}
        <a className="btn btn-ghost" href="/api/issues/export.csv" download title="Everything, as a spreadsheet">⬇ Export CSV</a>
      </div>
      <p className="muted small">Games that did not work in 💻 Browser Play — reported automatically (won't load, never starts, hangs) or with ⚑ Report in the game bar.
        Each report keeps what was happening at that moment so it can be investigated later.</p>
      {gameId && <GearSuggestions context="game" gameId={gameId} title="🛒 Hardware that may help" />}
      {!issues ? <Spinner /> : !issues.length ? <Empty>{filter === 'active' ? 'No open problems. 🎉' : 'Nothing here.'}</Empty> : (
        <div className="issue-list">
          {issues.map((i) => <IssueCard key={i.id} issue={i} onChange={replace} onDelete={() => setIssues(issues.filter((x) => x.id !== i.id))}
            onPlay={() => i.gameId && navigate(`/emulate/${i.gameId}`)} onGame={() => i.gameId && navigate(`/games/${i.gameId}`)} />)}
        </div>
      )}
    </div>
  )
}

function IssueCard({ issue: i, onChange, onDelete, onPlay, onGame }: {
  issue: Issue; onChange: (i: Issue) => void; onDelete: () => void; onPlay: () => void; onGame: () => void
}) {
  const toast = useToast()
  const [resolution, setResolution] = useState(i.resolution ?? '')
  const [busy, setBusy] = useState(false)
  const update = async (body: { status?: string; resolution?: string }) => {
    try { onChange(await api.updateIssue(i.id, body)) } catch (e) { toast(errorMessage(e), 'error') }
  }
  const investigate = async () => {
    setBusy(true)
    try { onChange(await api.investigateIssue(i.id)) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const remove = async () => {
    if (!window.confirm('Delete this report?')) return
    await api.deleteIssue(i.id).catch(() => {})
    onDelete()
  }
  const d = i.diagnostics
  return (
    <Card className="issue">
      <div>{i.coverUrl ? <img src={i.coverUrl} alt="" className="cover-img" style={{ width: 72, borderRadius: 8 }} /> : <div className="muted">🎮</div>}</div>
      <div style={{ display: 'grid', gap: '.4rem', minWidth: 0 }}>
        <div className="issue-head">
          <strong>{i.gameId ? <button className="linklike" onClick={onGame}>{i.title}</button> : i.title}</strong>
          <span className="issue-badge cat">{i.categoryLabel}</span>
          <span className={`issue-badge st-${i.status}`}>{STATUS_LABEL[i.status]}</span>
          <span className="issue-badge">{i.source === 'auto' ? '🤖 detected' : '⚑ reported'}{i.occurrences > 1 ? ` · ${i.occurrences}×` : ''}</span>
        </div>
        <span className="muted small">
          {when(i.createdAt)}{i.lastSeenAt && i.lastSeenAt !== i.createdAt ? ` · last ${when(i.lastSeenAt)}` : ''}
          {i.fileName ? ` · ${i.fileName}` : ''}{d.format ? ` (${String(d.format).toUpperCase()})` : ''}
          {i.sha256 ? ` · #${i.sha256.slice(0, 10)}` : ''}
        </span>
        {i.workedAt && <span className="small">✅ Reached gameplay since, {when(i.workedAt)} — maybe intermittent, or fixed.</span>}
        {i.note && <p className="small" style={{ margin: 0 }}>💬 {i.note}</p>}
        <div className="small"><strong>Likely causes</strong>
          <ul style={{ margin: '.2rem 0' }}>{i.likelyCauses.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
        {i.analysis && (
          <div className="small emu-hint-item">
            <strong>🔍 {i.analysis.likelyCause} <span className="muted">({i.analysis.confidence} confidence{i.analysis.model ? ` · ${i.analysis.model}` : ''})</span></strong>
            <span>{i.analysis.summary}</span>
            {i.analysis.steps.length > 0 && <ol style={{ margin: '.2rem 0' }}>{i.analysis.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
          </div>
        )}
        {i.hasScreenshot && <img className="issue-shot" src={`/api/issues/${i.id}/screenshot`} alt="The C64 screen when it was reported" loading="lazy" />}
        <details className="small"><summary>Diagnostics</summary><pre>{JSON.stringify(d, null, 2)}</pre></details>
        <div className="issue-row">
          <select value={i.status} onChange={(e) => update({ status: e.target.value })} aria-label="Status">
            {Object.entries(STATUS_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <input value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="What fixed it / what you found"
            onBlur={() => resolution !== (i.resolution ?? '') && update({ resolution })} style={{ flex: 1, minWidth: 180 }} maxLength={1000} />
        </div>
        <div className="issue-row">
          {i.gameId && <button className="btn btn-sm" onClick={onPlay}>▶ Try again in the browser</button>}
          <button className="btn btn-sm" onClick={investigate} disabled={busy} title="Ask the AI for the likely cause and next steps">{busy ? '🔍 Investigating…' : '🔍 Investigate'}</button>
          <button className="btn btn-ghost btn-sm" onClick={remove}>Delete</button>
        </div>
      </div>
    </Card>
  )
}
