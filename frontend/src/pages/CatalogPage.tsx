import { Fragment, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Empty, Spinner, formatBytes } from '../components/common'
import { useToast } from '../components/Toasts'
import { PlayChoice, PlayModesHint } from '../components/PlayChoice'
import { ElsewhereLinks } from '../components/Elsewhere'
import { ArchiveSearch } from '../components/ArchiveSearch'
import type { ElsewhereResult } from '../components/Elsewhere'
import { api, errorMessage } from '../services/api'
import type { CatalogItem } from '../shared/types'

const KINDS = [
  { value: 'games', label: 'Games' }, { value: 'music', label: 'Music (SID)' },
  { value: 'demos', label: 'Demos' }, { value: 'tools', label: 'Tools' }, { value: 'all', label: 'Everything' },
]

export function CatalogPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [cfg, setCfg] = useState<{ configured: boolean; url: string; clientId: string; cacheDir: string } | null>(null)
  const [q, setQ] = useState(params.get('q') ?? '')
  const [kind, setKind] = useState(params.get('kind') ?? 'games')
  const [results, setResults] = useState<CatalogItem[] | null>(null)
  const [elsewhere, setElsewhere] = useState<{ title: string; results: ElsewhereResult[] } | null>(null)
  const [searchingElsewhere, setSearchingElsewhere] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [files, setFiles] = useState<Record<string, { id: number; path: string; size: number; selected: boolean }[]>>({})
  const [edit, setEdit] = useState({ url: '', clientId: '' })

  useEffect(() => {
    api.catalog().then((c) => { setCfg(c); setEdit({ url: c.url, clientId: c.clientId }) }).catch(() => {})
  }, [])

  const search = async (query = q, k = kind) => {
    if (!query.trim()) return
    setParams({ q: query, kind: k }, { replace: true })
    setLoading(true)
    try {
      setResults(await api.catalogSearch(query.trim(), k))
    } catch (e) {
      toast(errorMessage(e), 'error')
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (params.get('q') && cfg?.configured) search(params.get('q')!, params.get('kind') ?? 'games')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg?.configured])

  const key = (r: CatalogItem) => `${r.category}-${r.id}`

  const play = async (r: CatalogItem) => {
    setBusy(key(r))
    try {
      await api.catalogPlay(r)
      toast(`📺 Loading ${r.name.replace(/_/g, ' ')} on your C64…`, 'ok')
      navigate('/stream')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(null)
    }
  }

  // Download into the library (if needed), then run it in the browser emulator on this device.
  const playHere = async (r: CatalogItem) => {
    setBusy(key(r))
    try {
      const res = r.gameId ? { gameId: r.gameId } : await api.catalogFetch(r)
      navigate(`/emulate/${res.gameId}`)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(null)
    }
  }

  const add = async (r: CatalogItem) => {
    setBusy(key(r))
    try {
      const res = await api.catalogFetch(r)
      toast(`${r.name} added to your library`, 'ok')
      setResults((list) => list?.map((x) => (key(x) === key(r) ? { ...x, gameId: res.gameId } : x)) ?? null)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(null)
    }
  }

  const showFiles = async (r: CatalogItem) => {
    if (files[key(r)]) {
      setFiles((f) => { const next = { ...f }; delete next[key(r)]; return next })
      return
    }
    try {
      const list = await api.catalogEntries(r.category, r.id)
      setFiles((f) => ({ ...f, [key(r)]: list }))
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const saveCfg = async () => {
    try {
      await api.saveSettings({ ASSEMBLY64_URL: edit.url.trim(), ASSEMBLY64_CLIENT_ID: edit.clientId.trim() })
      const c = await api.catalog()
      setCfg(c)
      toast('Catalog settings saved', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Online catalog</h1>
        <span className="muted">Assembly64 · CSDB, Gamebase64, OneLoad64, HVSC and more</span>
      </div>
      <PlayModesHint />

      {cfg && !cfg.configured && (
        <div className="banner banner-warn">
          The Assembly64 catalog needs a <strong>Client-Id</strong> it accepts (unknown ids are rejected with HTTP 464).
          Spiffy firmware uses <code>Spiffy</code>. Set it below.
        </div>
      )}

      <form className="filters" onSubmit={(e) => { e.preventDefault(); search() }}>
        <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles, e.g. Bruce Lee…"
          aria-label="Search the catalog" disabled={!cfg?.configured} />
        <select value={kind} onChange={(e) => { setKind(e.target.value); if (q) search(q, e.target.value) }} aria-label="Kind">
          {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
        <button className="btn btn-primary" disabled={!q.trim() || !cfg?.configured || loading}>{loading ? 'Searching…' : 'Search'}</button>
      </form>

      {loading && <div className="center"><Spinner /></div>}
      {results && !loading && (results.length ? (
        <Card>
          <div className="table-wrap">
            <table className="catalog">
              <thead><tr><th aria-label="Art" /><th>Title</th><th>By</th><th>Year</th><th>Source</th><th /></tr></thead>
              <tbody>
                {results.map((r) => (
                  <Fragment key={key(r)}>
                    <tr>
                      <td className="art-cell">{r.category <= 10 && <img src={`/api/art/csdb/${r.id}`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />}</td>
                      <td><strong>{r.name.replace(/_/g, ' ')}</strong>
                        {(r.category === 33 || r.category === 10 || /easyflash/i.test(r.name)) && (
                          <span className="tag best-browser" title="One-file cartridge release: starts instantly and plays best with 💻 In browser">⭐ best in browser</span>
                        )}</td>
                      <td className="muted">{r.group?.replace(/_/g, ' ') ?? '—'}</td>
                      <td className="muted">{r.year || '—'}</td>
                      <td><span className={`tag src-tag src-${r.kind}`}>{r.source}</span></td>
                      <td className="row-btns">
                        <PlayChoice onC64={() => play(r)} onBrowser={() => playHere(r)} busy={busy === key(r)}
                          browserOk={r.kind !== 'music'} />
                        {r.gameId
                          ? <button className="btn btn-sm" onClick={() => navigate(`/games/${r.gameId}`)}>In library →</button>
                          : <button className="btn btn-sm" disabled={busy === key(r)} onClick={() => add(r)}>+ Library</button>}
                        <button className="btn btn-ghost btn-sm" onClick={() => showFiles(r)}>Files</button>
                      </td>
                    </tr>
                    {files[key(r)] && (
                      <tr className="row-detail"><td colSpan={6}>
                        {files[key(r)].map((f) => (
                          <div key={f.id} className="small mono">
                            {f.selected ? '✓' : '·'} {f.path} <span className="muted">({formatBytes(f.size)})</span>
                          </div>
                        ))}
                        <div className="muted small">✓ = downloaded when you play. TAP files can't be started over the REST API.</div>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <ArchiveSearch query={(params.get('q') ?? '').trim()} />
        </Card>
      ) : (
        <Card>
          <Empty>Not in the Assembly64 catalog.</Empty>
          <ArchiveSearch query={(params.get('q') ?? '').trim()} auto />
          <div className="row-actions center-row">
            <button className="btn btn-primary" disabled={searchingElsewhere || !q.trim()} onClick={async () => {
              setSearchingElsewhere(true)
              try { setElsewhere(await api.findSources(q.trim())) } catch (e) { toast(errorMessage(e), 'error') } finally { setSearchingElsewhere(false) }
            }} title="New and homebrew games are often only on itch.io, CSDb or Lemon64">
              {searchingElsewhere ? '🔎 Searching…' : '🔎 Search itch.io, CSDb & Lemon64'}</button>
          </div>
          {elsewhere && (
            elsewhere.results.length
              ? <div className="ask-game"><span className="ask-game-title">{elsewhere.title}
                  <span className="muted small"> · {elsewhere.results.map((r) => r.label).join(', ')}</span></span>
                  {elsewhere.results.some((r) => r.catalog)
                    ? <PlayChoice onC64={() => play(elsewhere.results.find((r) => r.catalog)!.catalog!)} onBrowser={() => playHere(elsewhere.results.find((r) => r.catalog)!.catalog!)} />
                    : <ElsewhereLinks title={elsewhere.title} results={elsewhere.results} />}</div>
              : <p className="muted small">Nothing found on itch.io, CSDb or Lemon64 either.</p>
          )}
        </Card>
      ))}

      <Card title="Catalog settings">
        <div className="form-grid">
          <label className="field field-wide"><span>Catalog URL</span>
            <input value={edit.url} onChange={(e) => setEdit({ ...edit, url: e.target.value })} /></label>
          <label className="field"><span>Client-Id</span>
            <input value={edit.clientId} onChange={(e) => setEdit({ ...edit, clientId: e.target.value })} placeholder="Spiffy" /></label>
        </div>
        <div className="row-actions">
          <button className="btn btn-primary" onClick={saveCfg}
            disabled={!cfg || (edit.url === cfg.url && edit.clientId === cfg.clientId)}>Save</button>
        </div>
        <p className="muted small">
          Played titles are downloaded to <code>{cfg?.cacheDir}</code> and added to your library, so the next “Play” is
          instant. Saying “Play &lt;title&gt;” in the command bar searches here automatically when a title isn’t in your
          library, preferring curated Gamebase64 / OneLoad64 releases.
        </p>
      </Card>
    </div>
  )
}
