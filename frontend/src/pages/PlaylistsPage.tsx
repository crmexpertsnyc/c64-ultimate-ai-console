import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { CoverArt, titleArt } from '../components/CoverArt'
import { ElsewhereLinks } from '../components/Elsewhere'
import type { ElsewhereResult } from '../components/Elsewhere'
import { PlayChoice } from '../components/PlayChoice'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'
import type { CatalogItem, Game } from '../shared/types'

export interface PlaylistItem {
  title: string
  players: string | null
  minutes: number | null
  note: string | null
  done: boolean
  gameId: number | null
  catalog: CatalogItem | null
  browserOk: boolean
  coverUrl?: string | null
  matchedTitle?: string
  elsewhere?: ElsewhereResult[]
}
export interface PlaylistSummary {
  id: number; name: string; prompt: string; description: string | null; createdAt: string | null
  count: number; done: number; minutes: number; covers: string[]
}
export interface Playlist extends PlaylistSummary { items: PlaylistItem[] }

/** 🎉 Game nights & playlists: describe the occasion, the AI builds an ordered list you can play straight away. */
export function PlaylistsPage() {
  const { id } = useParams()
  return id ? <PlaylistView id={Number(id)} /> : <PlaylistList />
}

function PlaylistList() {
  const navigate = useNavigate()
  const toast = useToast()
  const [data, setData] = useState<{ playlists: PlaylistSummary[]; suggestions: string[] } | null>(null)
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.playlists().then(setData).catch((e) => toast(errorMessage(e), 'error')) }, [toast])

  const make = async (text: string) => {
    if (text.trim().length < 3) return
    setBusy(true)
    try {
      const p = await api.makePlaylist(text.trim())
      toast(`🎉 ${p.name} — ${p.count} games`, 'ok')
      navigate(`/playlists/${p.id}`)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <div className="page-head"><h1>🎉 Game nights &amp; playlists</h1></div>
      <Card>
        <form className="commandbar-form" onSubmit={(e) => { e.preventDefault(); make(prompt) }}>
          <input value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={300} disabled={busy}
            placeholder="e.g. “4-player party pack for Friday”, “30-minute lunch break”, “best C64 soundtracks”" />
          <button className="btn btn-primary" disabled={busy || prompt.trim().length < 3}>{busy ? '✨ Planning…' : '✨ Make it'}</button>
        </form>
        <div className="chips">
          {data?.suggestions.map((sg) => <button key={sg} className="chip" disabled={busy} onClick={() => { setPrompt(sg); make(sg) }}>{sg}</button>)}
        </div>
        <p className="muted small">The AI picks real C64 games — from your library and taste first — puts them in a good order, and notes players, time and controllers for each.</p>
      </Card>
      {!data ? <Spinner /> : !data.playlists.length ? <Empty>No playlists yet — make one above.</Empty> : (
        <div className="playlist-grid">
          {data.playlists.map((p) => (
            <button key={p.id} className="card playlist-card" onClick={() => navigate(`/playlists/${p.id}`)}>
              <div className="playlist-covers">{p.covers.map((c) => <img key={c} src={c} alt="" loading="lazy" />)}</div>
              <strong>{p.name}</strong>
              <span className="muted small">{p.count} games{p.minutes ? ` · about ${Math.round(p.minutes / 5) * 5} min` : ''}{p.done ? ` · ${p.done} played` : ''}</span>
              {p.description && <span className="small">{p.description}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function PlaylistView({ id }: { id: number }) {
  const navigate = useNavigate()
  const toast = useToast()
  const [p, setP] = useState<Playlist | null>(null)
  useEffect(() => { api.playlist(id).then(setP).catch((e) => toast(errorMessage(e), 'error')) }, [id, toast])
  if (!p) return <div className="center"><Spinner /></div>

  const onC64 = async (it: PlaylistItem) => {
    try {
      if (it.gameId) await api.play(it.gameId)
      else if (it.catalog) await api.catalogPlay(it.catalog)
      navigate('/stream')
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const inBrowser = async (it: PlaylistItem) => {
    try {
      const gid = it.gameId ?? (it.catalog ? (await api.catalogFetch(it.catalog)).gameId : null)
      if (gid) navigate(`/emulate/${gid}`)
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const item = async (index: number, body: { done?: boolean; remove?: boolean }) => {
    try { setP(await api.playlistItem(p.id, index, body)) } catch (e) { toast(errorMessage(e), 'error') }
  }
  const remove = async () => {
    if (!window.confirm(`Delete “${p.name}”?`)) return
    await api.deletePlaylist(p.id).catch(() => {})
    navigate('/playlists')
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>🎉 {p.name}</h1>
        <button className="btn btn-ghost" onClick={() => navigate('/playlists')}>← All playlists</button>
        <button className="btn btn-ghost" onClick={remove}>Delete</button>
      </div>
      <p className="muted">{p.description} <span className="small">· “{p.prompt}”{p.minutes ? ` · about ${Math.round(p.minutes / 5) * 5} min` : ''}</span></p>
      <ol className="playlist-items">
        {p.items.map((it, i) => (
          <li key={`${it.title}-${i}`} className={`playlist-item ${it.done ? 'done' : ''}`}>
            <span className="playlist-n">{i + 1}</span>
            <CoverArt game={{ id: it.gameId ?? 0, title: it.matchedTitle ?? it.title, format: '',
              coverUrl: it.coverUrl ?? titleArt(it.matchedTitle ?? it.title, it.catalog) } as unknown as Game} />
            <div className="playlist-body">
              <strong>{it.title}</strong>
              <span className="muted small">{[it.players && `👥 ${it.players}`, it.minutes && `⏱ ${it.minutes} min`].filter(Boolean).join(' · ')}</span>
              {it.note && <span className="small">{it.note}</span>}
              <div className="foryou-actions">
                {(it.gameId || it.catalog)
                  ? <PlayChoice onC64={() => onC64(it)} onBrowser={() => inBrowser(it)} browserOk={it.browserOk} />
                  : it.elsewhere?.length ? <ElsewhereLinks title={it.title} results={it.elsewhere} />
                    : <button className="btn btn-sm" onClick={() => navigate(`/catalog?q=${encodeURIComponent(it.title)}&kind=games`)}>🔎 Find it</button>}
                <button className={`btn btn-sm ${it.done ? 'btn-primary' : 'btn-ghost'}`} onClick={() => item(i, { done: !it.done })}>{it.done ? '✓ Played' : 'Mark played'}</button>
                <button className="btn btn-ghost btn-sm" onClick={() => item(i, { remove: true })} title="Remove from the playlist">✕</button>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
