import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { CatalogItem, Game } from '../shared/types'
import { Card, Modal } from './common'
import { CoverArt, titleArt } from './CoverArt'
import { ElsewhereLinks } from './Elsewhere'
import type { ElsewhereResult } from './Elsewhere'
import { PlayChoice } from './PlayChoice'
import { Thumbs } from './Thumbs'
import { useToast } from './Toasts'

export interface Pick {
  title: string
  reason: string
  because: string[]
  kind: 'match' | 'gem' | 'different'
  gameId: number | null
  catalog: CatalogItem | null
  browserOk: boolean
  coverUrl?: string | null
  matchedTitle?: string
  elsewhere?: ElsewhereResult[]
  rating: -1 | 0 | 1
}
export interface TasteProfile {
  liked: { title: string; gameId: number | null; score: number; why: string[] }[]
  disliked: string[]
  searches: string[]
  genres: string[]
  publishers: string[]
  decades: string[]
  events: number
  ratings: number
}
export interface Recommendations {
  picks: Pick[]
  again: { title: string; gameId: number; coverUrl: string | null; why: string[] }[]
  summary: string | null
  ai: boolean
  webSearch: boolean
  model: string | null
  note: string | null
  stale: boolean
  generatedAt: number
  profile: TasteProfile
}

const KIND_LABEL: Record<Pick['kind'], string | null> = { match: null, gem: '💎 Hidden gem', different: '🎲 Something different' }
const AUTO_REFRESH_MIN = 30 // new picks by themselves at most this often; the ↻ button any time

/** ✨ For you — games picked from what you search for, play, favorite and rate (👍 / 👎). */
export function ForYou() {
  const navigate = useNavigate()
  const toast = useToast()
  const [data, setData] = useState<Recommendations | null>(null)
  const [busy, setBusy] = useState(false)
  const [learned, setLearned] = useState(false)

  const refresh = useCallback(async () => {
    setBusy(true)
    try { setData(await api.refreshRecommendations()) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }, [toast])

  useEffect(() => {
    api.recommendations().then((r) => {
      setData(r)
      const ageMin = (Date.now() / 1000 - (r.generatedAt || 0)) / 60
      if (r.stale && (!r.picks.length || ageMin > AUTO_REFRESH_MIN)) refresh()
    }).catch(() => {})
  }, [refresh])

  const onC64 = async (p: Pick) => {
    try {
      if (p.gameId) await api.play(p.gameId)
      else if (p.catalog) await api.catalogPlay(p.catalog)
      toast(`📺 Loading ${p.matchedTitle ?? p.title} on your C64…`, 'ok')
      navigate('/stream')
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const inBrowser = async (p: Pick) => {
    try {
      const id = p.gameId ?? (p.catalog ? (await api.catalogFetch(p.catalog)).gameId : null)
      if (id) navigate(`/emulate/${id}`)
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const rated = (p: Pick, v: -1 | 0 | 1) => {
    if (!data) return
    setData({ ...data, stale: true, picks: v === -1 ? data.picks.filter((x) => x.title !== p.title) : data.picks.map((x) => (x.title === p.title ? { ...x, rating: v } : x)) })
  }

  const profile = data?.profile
  const basedOn = profile?.liked.slice(0, 4).map((x) => x.title) ?? []
  const actions = (
    <span className="row-actions">
      {profile && profile.events + profile.ratings > 0 && (
        <button className="btn btn-ghost btn-sm" onClick={() => setLearned(true)} title="What the console has learned about your taste">🧠 What I've learned</button>
      )}
      <button className="btn btn-ghost btn-sm" onClick={refresh} disabled={busy} title="Make new picks">{busy ? '✨ Thinking…' : '↻'}</button>
    </span>
  )

  return (
    <Card title="✨ For you" actions={actions} className="foryou">
      {data?.summary && <p className="foryou-summary">{data.summary}</p>}
      {basedOn.length > 0 && (
        <p className="muted small">Based on {basedOn.join(', ')}{profile && profile.searches.length ? ` and what you search for` : ''} · 👍 / 👎 to teach it</p>
      )}
      {!data || (busy && !data.picks.length) ? (
        <p className="muted">{busy ? '✨ Picking games for you…' : 'Loading…'}</p>
      ) : !data.picks.length ? (
        <p className="muted">Play a few games or give them a 👍 and I'll learn what you like.</p>
      ) : (
        <div className="foryou-grid">
          {data.picks.map((p) => (
            <div key={p.title} className="foryou-card">
              <button className="foryou-cover" onClick={() => p.gameId && navigate(`/games/${p.gameId}`)} disabled={!p.gameId}
                title={p.gameId ? 'Open the game page' : undefined}>
                <CoverArt game={{ id: p.gameId ?? 0, title: p.matchedTitle ?? p.title, format: '',
                  // catalog picks: the release's CSDb screenshot (falls back to the placeholder if missing)
                  coverUrl: p.coverUrl ?? titleArt(p.matchedTitle ?? p.title, p.catalog) } as unknown as Game} />
              </button>
              <div className="foryou-body">
                <strong>{p.title}</strong>
                {KIND_LABEL[p.kind] && <span className="foryou-kind">{KIND_LABEL[p.kind]}</span>}
                <p className="small">{p.reason}</p>
                {p.because.length > 0 && <p className="muted small">Because you liked {p.because.join(' and ')}</p>}
                <div className="foryou-actions">
                  {(p.gameId || p.catalog)
                    ? <PlayChoice onC64={() => onC64(p)} onBrowser={() => inBrowser(p)} browserOk={p.browserOk} />
                    : p.elsewhere?.length ? <ElsewhereLinks title={p.title} results={p.elsewhere} />
                      : <button className="btn btn-sm" onClick={() => navigate(`/catalog?q=${encodeURIComponent(p.title)}&kind=games`)}>🔎 Find it</button>}
                  <Thumbs title={p.title} gameId={p.gameId} value={p.rating} compact onChange={(v) => rated(p, v)} />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {data && data.again.length > 0 && (
        <div className="foryou-again">
          <h4>▶ Play again</h4>
          <div className="shelf">
            {data.again.map((g) => (
              <button key={g.gameId} className="shelf-item" onClick={() => navigate(`/games/${g.gameId}`)} title={g.why.join(' · ')}>
                <CoverArt game={{ id: g.gameId, title: g.title, coverUrl: g.coverUrl, format: '' } as unknown as Game} />
                <span>{g.title}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {data && (
        <p className="muted small foryou-foot">
          {data.ai ? `${data.webSearch ? '🔎 Web + ' : ''}🤖 ${data.model || 'AI'}` : '📚 From your library'}
          {data.note ? ` · ${data.note}` : ''}
          {data.stale && !busy && data.picks.length > 0 ? <> · <button className="linklike" onClick={refresh}>new picks available ↻</button></> : null}
        </p>
      )}
      {learned && profile && <Learned profile={profile} onClose={() => setLearned(false)} onChanged={() => { setLearned(false); refresh() }} />}
    </Card>
  )
}

/** Everything the console has learned — visible and removable. */
function Learned({ profile, onClose, onChanged }: { profile: TasteProfile; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const [p, setP] = useState(profile)
  const forget = async (title: string) => {
    await api.forgetGame(title).catch(() => {})
    setP({ ...p, liked: p.liked.filter((x) => x.title !== title), disliked: p.disliked.filter((x) => x !== title) })
    toast(`Forgot ${title}`, 'ok')
  }
  const clearAll = async () => {
    if (!window.confirm('Forget all your searches, plays and ratings? Recommendations start over.')) return
    await api.clearTaste().catch(() => {})
    toast('Taste history cleared', 'ok')
    onChanged()
  }
  return (
    <Modal open title="🧠 What I've learned about your taste" onClose={onClose}
      footer={<><button className="btn btn-ghost" onClick={clearAll}>Forget everything</button><button className="btn btn-primary" onClick={onChanged}>Done — new picks</button></>}>
      <p className="muted small">Learned from your searches, questions, games you play (and for how long), favorites and 👍 / 👎. It stays on this console.</p>
      {p.liked.length > 0 && (
        <>
          <h4>You like</h4>
          <ul className="learned-list">
            {p.liked.map((x) => (
              <li key={x.title}><span>{x.title}</span><span className="muted small">{x.why.join(' · ')}</span>
                <button className="btn btn-ghost btn-sm" onClick={() => forget(x.title)} title="Forget this game">✕</button></li>
            ))}
          </ul>
        </>
      )}
      {(p.genres.length > 0 || p.publishers.length > 0 || p.decades.length > 0) && (
        <p className="small">
          {p.genres.length > 0 && <>🎮 {p.genres.join(', ')} </>}
          {p.publishers.length > 0 && <>· 🏢 {p.publishers.join(', ')} </>}
          {p.decades.length > 0 && <>· 📅 {p.decades.join(', ')}</>}
        </p>
      )}
      {p.disliked.length > 0 && (
        <>
          <h4>Not for you</h4>
          <ul className="learned-list">
            {p.disliked.map((t) => (
              <li key={t}><span>{t}</span><span className="muted small">👎</span>
                <button className="btn btn-ghost btn-sm" onClick={() => forget(t)} title="Remove the 👎">✕</button></li>
            ))}
          </ul>
        </>
      )}
      {p.searches.length > 0 && (
        <>
          <h4>You've been looking for</h4>
          <p className="small">{p.searches.map((s) => `“${s}”`).join(' · ')}</p>
        </>
      )}
    </Modal>
  )
}
