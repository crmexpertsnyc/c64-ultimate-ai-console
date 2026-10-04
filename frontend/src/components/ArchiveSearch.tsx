import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import { PlayChoice } from './PlayChoice'
import { useToast } from './Toasts'

export interface ArchiveResult {
  source: 'archive' | 'c64com' | 'gtw'
  id: string
  title: string
  label: string
  year: number | null
  publisher: string | null
  note: string | null
  format: string | null
  url: string
  playable: boolean | null   // null = unknown until tried (Games That Weren't: only some entries have files)
}
export interface ArchiveSearchResult {
  query: string
  results: ArchiveResult[]
  errors: Record<string, string>
  linkOuts: { source: string; label: string; url: string }[]
}

const SOURCE_TIP: Record<string, string> = {
  archive: 'Internet Archive — the C64 Software Library and the Ultimate Tape Archive (original tapes)',
  c64com: 'C64.com — game archive with downloads',
  gtw: "Games That Weren't — unreleased, cancelled and recovered games",
}

/** One archive result: add it to the library (downloads the file), then play it on the C64 or here. */
export function ArchivePlay({ source, id, title }: { source: ArchiveResult['source']; id: string; title: string }) {
  const navigate = useNavigate()
  const toast = useToast()
  const [gameId, setGameId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  if (gameId) {
    return (
      <PlayChoice onC64={() => api.play(gameId).then(() => navigate('/stream')).catch((e) => toast(errorMessage(e), 'error'))}
        onBrowser={() => navigate(`/emulate/${gameId}`)} />
    )
  }
  return (
    <button className="btn btn-sm btn-primary" disabled={busy} title="Download it into your library, then play it on your C64 or in the browser"
      onClick={async () => {
        setBusy(true)
        try {
          const r = await api.importSource(source, id, title)
          setGameId(r.gameId)
          toast(`${title} added to your library`, 'ok')
        } catch (e) {
          toast(errorMessage(e), 'error')
        } finally {
          setBusy(false)
        }
      }}>{busy ? '⬇ Adding…' : '⬇ Add & play'}</button>
  )
}

/** Search the online archives (Internet Archive, C64.com, Games That Weren't) plus links to the rest. */
export function ArchiveSearch({ query, auto = false }: { query: string; auto?: boolean }) {
  const toast = useToast()
  const [res, setRes] = useState<ArchiveSearchResult | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!query.trim()) return
    setBusy(true)
    try { setRes(await api.searchSources(query.trim())) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  useEffect(() => { setRes(null); if (auto) run() }, [query, auto]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!res) {
    return (
      <div className="row-actions center-row">
        <button className="btn" disabled={busy || !query.trim()} onClick={run}
          title="Internet Archive (incl. the Ultimate Tape Archive), C64.com and Games That Weren't">
          {busy ? '🌐 Searching archives…' : '🌐 Search online archives'}</button>
      </div>
    )
  }
  return (
    <div className="archive-search">
      <h4>🌐 Online archives <span className="muted small">for “{res.query}”</span></h4>
      {res.results.length === 0 && <p className="muted small">Nothing in the Internet Archive, C64.com or Games That Weren't.</p>}
      {res.results.slice(0, 12).map((r) => (
        <div key={`${r.source}:${r.id}`} className="ask-game">
          <span className="ask-game-title">
            {r.title}
            <span className="muted small" title={SOURCE_TIP[r.source]}>
              {' · '}{r.label}{r.year ? ` · ${r.year}` : ''}{r.publisher ? ` · ${r.publisher}` : ''}{r.note ? ` · ${r.note}` : ''}
              {r.format ? ` · .${r.format}` : ''}</span>
          </span>
          <span className="row-actions">
            <ArchivePlay source={r.source} id={r.id} title={r.title.replace(/\s*[([].*$/, '') || r.title} />
            <a className="btn btn-ghost btn-sm" href={r.url} target="_blank" rel="noopener noreferrer" title="Open the page in a new tab">↗</a>
          </span>
        </div>
      ))}
      {Object.keys(res.errors).length > 0 && (
        <p className="muted small">Not reachable right now: {Object.keys(res.errors).map((k) => SOURCE_TIP[k]?.split(' —')[0] ?? k).join(', ')}</p>
      )}
      <p className="small">Also look on: {res.linkOuts.map((l) => (
        <a key={l.source} className="btn btn-ghost btn-sm" href={l.url} target="_blank" rel="noopener noreferrer"
          title={l.source === 'gb64' ? 'GameBase64 — details, versions and credits (no downloads)' : l.source === 'csdb'
            ? 'CSDb — scene releases, cracks and trainers' : 'Lemon64 — reviews and ratings'}>{l.label} ↗</a>
      ))}</p>
    </div>
  )
}
