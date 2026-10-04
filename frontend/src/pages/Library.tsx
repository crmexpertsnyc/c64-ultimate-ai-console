import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { CoverArt } from '../components/CoverArt'
import { PlayChoice, PlayModesHint, canPlayInBrowser } from '../components/PlayChoice'
import { importFile } from '../components/Elsewhere'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { DetailsJob, LibraryQuery } from '../services/api'
import type { Game } from '../shared/types'

const PAGE = 48

export function Library() {
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState<Game[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [facets, setFacets] = useState<{ formats: string[]; publishers: string[]; years: number[]; genres: string[]; categories: string[] } | null>(null)
  const [q, setQ] = useState(params.get('q') ?? '')
  const navigate = useNavigate()
  const toast = useToast()
  const { scan, covers, coverVersion } = useLive()

  const [details, setDetails] = useState<DetailsJob | null>(null)
  const query: LibraryQuery = useMemo(() => ({
    q: params.get('q') ?? undefined,
    favorites: params.get('favorites') === '1',
    recent: params.get('recent') === '1',
    format: params.get('format') ?? undefined,
    publisher: params.get('publisher') ?? undefined,
    year: params.get('year') ? Number(params.get('year')) : undefined,
    genre: params.get('genre') ?? undefined,
    category: params.get('category') ?? undefined,
    multiplayer: params.get('multiplayer') === '1' ? true : undefined,
    joystick_port: params.get('port') ? Number(params.get('port')) : undefined,
    offset: Number(params.get('offset') ?? 0),
    limit: PAGE,
  }), [params])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.library(query)
      .then((r) => { if (!cancelled) { setItems(r.items); setTotal(r.total) } })
      .catch((e) => toast(errorMessage(e), 'error'))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [query, toast, scan?.running, coverVersion, details?.running])

  useEffect(() => { api.facets().then(setFacets).catch(() => {}) }, [scan?.running, details?.running])

  // Debounced search box → URL.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((params.get('q') ?? '') !== q) set('q', q || null)
    }, 250)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  function set(key: string, value: string | null) {
    const next = new URLSearchParams(params)
    if (value === null || value === '') next.delete(key)
    else next.set(key, value)
    if (key !== 'offset') next.delete('offset')
    setParams(next, { replace: true })
  }
  const toggle = (key: string) => set(key, params.get(key) === '1' ? null : '1')

  const findCovers = async () => {
    try {
      const st = await api.refreshCovers()
      toast(`Looking for cover art for ${st.total} title${st.total === 1 ? '' : 's'}…`, 'info')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const favorite = async (g: Game) => {
    try {
      const r = await api.favorite(g.id)
      setItems((list) => list.map((x) => (x.id === g.id ? { ...x, favorite: r.favorite } : x)))
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const play = async (g: Game) => {
    try {
      await api.play(g.id)
      toast(`📺 Loading ${g.title} on your C64…`, 'ok')
      navigate('/stream')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const offset = query.offset ?? 0

  // ✨ Fill in details for the whole library (background job; progress polled).
  useEffect(() => {
    if (!details?.running) return
    const t = window.setInterval(() => {
      api.detailsJob().then((j) => {
        setDetails(j)
        if (!j.running) {
          toast(`✨ Details filled in for ${j.filled} game${j.filled === 1 ? '' : 's'}${j.errors ? ` · ${j.errors} not found` : ''}`, j.errors && !j.filled ? 'error' : 'ok')
        }
      }).catch(() => {})
    }, 2000)
    return () => window.clearInterval(t)
  }, [details?.running]) // eslint-disable-line react-hooks/exhaustive-deps
  const fillAll = async () => {
    try {
      const j = await api.fillAllDetails(true)
      if (!j.total) toast('✨ Every game already has details', 'ok')
      setDetails(j)
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  // ⬆ Add game file: a game downloaded from itch.io, a developer's site, Lemon64… (button or drag & drop).
  const picker = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const addFiles = async (files: FileList | File[] | null | undefined) => {
    for (const f of Array.from(files ?? [])) {
      try {
        const id = await importFile(f)
        toast(`⬆ ${f.name} added — play it 📺 on your C64 or 💻 in the browser`, 'ok')
        navigate(`/games/${id}`)
      } catch (e) {
        toast(errorMessage(e), 'error')
      }
    }
  }

  return (
    <div className={`page ${dragging ? 'drop-target' : ''}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true) } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false) }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}>
      <div className="page-head">
        <h1>Library</h1>
        <button className="btn btn-ghost" onClick={findCovers} disabled={covers?.running}
          title="Box art and title screens for every title (your own cover choices are kept)">
          {covers?.running ? `🖼 Finding art… ${covers.done}/${covers.total}` : '🖼 Find cover art'}
        </button>
        <button className="btn btn-ghost" onClick={fillAll} disabled={details?.running}
          title="✨ Look up genre, players, year, publisher and style tags (co-op, great music, relaxing…) for games that don't have them — web + AI. Your own edits are kept.">
          {details?.running ? `✨ Filling in… ${details.done}/${details.total}` : '✨ Fill in details'}
        </button>
        <button className="btn" onClick={() => picker.current?.click()}
          title="Add a game you downloaded (itch.io, CSDb, Lemon64, a developer's site…): .prg .d64 .crt .t64 .g64 .zip — or drop the file on this page">
          ⬆ Add game file</button>
        <input ref={picker} type="file" hidden multiple accept=".prg,.d64,.d71,.d81,.g64,.t64,.tap,.crt,.p00,.x64,.sid,.zip"
          onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
        <button className="btn btn-ghost" onClick={() => navigate('/compatibility')} title="Games that did not work in Browser Play, with diagnostics">🐞 Compatibility log</button>
        <span className="muted">{total} title{total === 1 ? '' : 's'}</span>
        {scan?.running && <span className="badge badge-unverified"><Spinner /> scanning…</span>}
      </div>
      <PlayModesHint />

      <div className="filters">
        <input className="search" placeholder="Search title or publisher…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        <button className={`chip ${query.favorites ? 'on' : ''}`} onClick={() => toggle('favorites')}>★ Favorites</button>
        <button className={`chip ${query.recent ? 'on' : ''}`} onClick={() => toggle('recent')}>⟲ Recently played</button>
        <button className={`chip ${query.multiplayer ? 'on' : ''}`} onClick={() => toggle('multiplayer')}>👥 Multiplayer</button>
        <select value={params.get('format') ?? ''} onChange={(e) => set('format', e.target.value || null)} aria-label="Format">
          <option value="">All formats</option>
          {facets?.formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
        </select>
        <select value={params.get('category') ?? ''} onChange={(e) => set('category', e.target.value || null)} aria-label="Category">
          <option value="">All types</option>
          {facets?.categories.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <select value={params.get('publisher') ?? ''} onChange={(e) => set('publisher', e.target.value || null)} aria-label="Publisher">
          <option value="">Any publisher</option>
          {facets?.publishers.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <select value={params.get('year') ?? ''} onChange={(e) => set('year', e.target.value || null)} aria-label="Year">
          <option value="">Any year</option>
          {facets?.years.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {!!facets?.genres.length && (
          <select value={params.get('genre') ?? ''} onChange={(e) => set('genre', e.target.value || null)} aria-label="Genre">
            <option value="">Any genre</option>
            {facets.genres.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
        <select value={params.get('port') ?? ''} onChange={(e) => set('port', e.target.value || null)} aria-label="Joystick port">
          <option value="">Any port</option>
          <option value="1">Joystick port 1</option>
          <option value="2">Joystick port 2</option>
        </select>
      </div>

      {loading && !items.length ? <div className="center"><Spinner /></div> : items.length === 0 ? (
        <Card>
          <Empty>
            No titles match. {total === 0 && !params.toString() && (
              <>Your library is empty — <button className="link" onClick={() => navigate('/settings#library')}>add a game folder</button>.</>
            )}
          </Empty>
        </Card>
      ) : (
        <div className="game-grid">
          {items.map((g) => (
            <article key={g.id} className="game-card">
              <button className="game-cover" onClick={() => navigate(`/games/${g.id}`)} aria-label={`Open ${g.title}`}>
                <CoverArt game={g} />
              </button>
              <div className="game-meta">
                <button className="game-title" onClick={() => navigate(`/games/${g.id}`)}>{g.title}</button>
                <div className="muted small">
                  {[g.publisher, g.year, g.format.toUpperCase(), g.numDisks > 1 ? `${g.numDisks} disks` : null].filter(Boolean).join(' · ')}
                </div>
              </div>
              <div className="game-actions">
                <PlayChoice onC64={() => play(g)} onBrowser={() => navigate(`/emulate/${g.id}`)}
                  browserOk={canPlayInBrowser(g.format, g.category)} />
                <button className={`btn btn-ghost btn-sm fav ${g.favorite ? 'on' : ''}`} onClick={() => favorite(g)}
                  aria-label={g.favorite ? 'Remove favorite' : 'Add favorite'}>{g.favorite ? '★' : '☆'}</button>
              </div>
            </article>
          ))}
        </div>
      )}

      {total > PAGE && (
        <div className="pager">
          <button className="btn" disabled={offset === 0} onClick={() => set('offset', String(Math.max(0, offset - PAGE)))}>← Prev</button>
          <span className="muted">{offset + 1}–{Math.min(total, offset + PAGE)} of {total}</span>
          <button className="btn" disabled={offset + PAGE >= total} onClick={() => set('offset', String(offset + PAGE))}>Next →</button>
        </div>
      )}
    </div>
  )
}
