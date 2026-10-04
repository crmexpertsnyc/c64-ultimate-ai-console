import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { PlayChoice } from '../components/PlayChoice'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'

export interface NewsItem {
  id: number
  kind: 'release' | 'news' | 'video' | 'product' | 'deal'
  source: string
  sourceLabel: string
  category: string | null
  title: string
  url: string
  summary: string | null
  image: string | null
  author: string | null
  releaseType: string | null
  csdbId: string | null
  videoId: string | null
  playable: boolean
  gameId: number | null
  publishedAt: string | null
  stats: { downloads?: number; comments?: number; votes?: number; rating?: number; ratings?: number; stars?: number; views?: number; likes?: number } | null
  popularity: number | null   // 0-100: share of this source's recent items it beats
  trend: number | null        // the same, per day since release (what's hot right now)
}
export interface NewsList {
  items: NewsItem[]
  counts: Record<string, number>
  state: { checkedAt: string | null; running: boolean; errors: Record<string, string>; intervalMinutes: number
    feeds: { source: string; label: string; kind: string }[] }
}
export interface Digest { headline: string; intro: string; picks: { item: NewsItem; why: string }[]; madeAt: string }

type Tab = 'release' | 'video' | 'news' | 'product'
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'release', label: '🆕 New releases', hint: 'New games, demos and music from the scene (CSDb) and homebrew developers (itch.io) — play them right here' },
  { id: 'video', label: '🎬 Watch', hint: 'New videos from C64 YouTube channels — watch them here, on the TV too' },
  { id: 'news', label: '📰 News', hint: 'C64 news articles' },
  { id: 'product', label: '🛒 New hardware', hint: 'New products in the C64 makers\' shops (Commodore, Protovision, Fusion Retro, VideoGamePerfection…)' },
]
const CATEGORIES = [
  ['', 'All'], ['game', '🎮 Games'], ['demo', '✨ Demos'], ['music', '🎵 Music'], ['graphics', '🎨 Graphics'], ['tool', '🛠 Tools'],
] as const

type Sort = 'newest' | 'popular' | 'trending'
const SORTS: { id: Sort; label: string; hint: string }[] = [
  { id: 'newest', label: '🕒 Newest', hint: 'Most recent first' },
  { id: 'popular', label: '🔥 Most popular', hint: 'CSDb downloads, comments, votes and rating · itch.io ratings · YouTube views and likes — ranked within each source' },
  { id: 'trending', label: '📈 Trending', hint: 'Popular for its age: what is catching on right now' },
]

const short = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n)

/** The numbers behind the popularity, as the source shows them. */
function StatLine({ item }: { item: NewsItem }) {
  const s = item.stats
  if (!s) return null
  const parts: string[] = []
  if (s.downloads) parts.push(`⬇ ${short(s.downloads)}`)
  if (s.rating) parts.push(`★ ${s.rating}/10 (${s.votes})`)
  else if (s.votes) parts.push(`🗳 ${s.votes} vote${s.votes === 1 ? '' : 's'}`)
  if (s.comments) parts.push(`💬 ${s.comments}`)
  if (s.ratings) parts.push(`★ ${s.stars}/5 (${s.ratings})`)
  if (s.views) parts.push(`👁 ${short(s.views)}`)
  if (s.likes) parts.push(`👍 ${short(s.likes)}`)
  return parts.length ? <span className="stat-line small" title={`Popularity on ${item.sourceLabel}`}>{parts.join(' · ')}</span> : null
}

function PopBadge({ item, sort }: { item: NewsItem; sort: Sort }) {
  const v = sort === 'trending' ? item.trend : item.popularity
  if (v === null || v < 75) return null
  const top = Math.max(1, Math.round(100 - v))
  return <span className={`badge-pop ${v >= 90 ? 'hot' : ''}`} title={`Top ${top}% of recent ${item.sourceLabel} ${item.kind === 'video' ? 'videos' : 'releases'}${sort === 'trending' ? ' for its age' : ''}`}>
    {sort === 'trending' ? '📈' : '🔥'} Top {top}%</span>
}

const SEEN_KEY = 'c64.newsSeenAt'
export function newsSeenAt(): string {
  try { return localStorage.getItem(SEEN_KEY) || new Date(Date.now() - 3 * 86400000).toISOString() } catch { return new Date().toISOString() }
}
function markSeen() {
  try { localStorage.setItem(SEEN_KEY, new Date().toISOString()) } catch { /* private mode */ }
}

function ago(iso: string | null): string {
  if (!iso) return ''
  const d = (Date.now() - new Date(iso).getTime()) / 86400000
  if (d < 1) return 'today'
  if (d < 2) return 'yesterday'
  if (d < 14) return `${Math.floor(d)} days ago`
  return new Date(iso).toLocaleDateString()
}

/** 📰 C64 news, 🆕 new releases you can play right away, and 🎬 videos — checked every 30 minutes. */
export function NewsPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'release'
  const category = params.get('category') || ''
  const sort = (params.get('sort') as Sort) || 'newest'
  const [q, setQ] = useState('')
  const [data, setData] = useState<NewsList | null>(null)
  const [busy, setBusy] = useState<number | 'refresh' | 'digest' | null>(null)
  const [digest, setDigest] = useState<Digest | null>(null)
  const [watching, setWatching] = useState<NewsItem | null>(null)
  const [seenAt] = useState(newsSeenAt)
  const { news } = useLive()

  const load = useCallback(() => {
    api.news({ kind: tab, category: tab === 'release' ? category || undefined : undefined, q: q.trim() || undefined, limit: 120,
      sort: tab === 'news' || tab === 'product' ? 'newest' : sort })
      .then(setData).catch((e) => toast(errorMessage(e), 'error'))
  }, [tab, category, q, sort, toast])
  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t) }, [load, q, news?.at])
  useEffect(() => { api.newsDigest().then((r) => setDigest(r.digest)).catch(() => {}); markSeen() }, [])

  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n })
  const refresh = async () => {
    setBusy('refresh')
    try {
      const r = await api.refreshNews()
      toast(r.added ? `🆕 ${r.added} new item${r.added === 1 ? '' : 's'}` : 'Up to date — nothing new right now', 'ok')
      load()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }
  const makeDigest = async () => {
    setBusy('digest')
    try { setDigest((await api.makeNewsDigest()).digest) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }
  /** Add a release to the library (downloaded from CSDb), then play it. */
  const play = async (item: NewsItem, where: 'c64' | 'browser') => {
    setBusy(item.id)
    try {
      const gameId = item.gameId ?? (await api.addNewsRelease(item.id)).gameId
      if (where === 'browser') navigate(`/emulate/${gameId}`)
      else { await api.play(gameId); navigate('/stream') }
    } catch (e) { toast(errorMessage(e), 'error'); load() } finally { setBusy(null) }
  }

  const fresh = (i: NewsItem) => !!i.publishedAt && i.publishedAt > seenAt
  const items = data?.items ?? []
  const st = data?.state
  return (
    <div className="page news-page">
      <header className="page-head">
        <div>
          <h1>📰 C64 News &amp; New Releases</h1>
          <p className="muted">New games, demos, music, videos and news from the Commodore 64 world — checked every {st?.intervalMinutes ?? 30} minutes.
            {st?.checkedAt ? ` Last checked ${new Date(st.checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.` : ''}
            {' '}<a href="/settings#updates">🔄 Sources & updates</a></p>
        </div>
        <div className="row-actions">
          <button className="btn btn-ghost" onClick={makeDigest} disabled={busy !== null}
            title="The AI reads this week's news and picks what's worth a look">{busy === 'digest' ? '🤖 Reading…' : '🤖 This week in C64'}</button>
          <button className="btn" onClick={refresh} disabled={busy !== null}>{busy === 'refresh' ? <Spinner /> : '↻'} Check now</button>
        </div>
      </header>

      {digest && (
        <Card title={`🤖 ${digest.headline}`} className="news-digest" actions={<button className="btn btn-ghost btn-sm" onClick={() => setDigest(null)}>✕</button>}>
          {digest.intro && <p>{digest.intro}</p>}
          <ul className="digest-picks">
            {digest.picks.map(({ item, why }) => (
              <li key={item.id}>
                <button className="link" onClick={() => item.kind === 'video' ? setWatching(item) : set('tab', item.kind)}>
                  {item.kind === 'video' ? '🎬' : item.kind === 'news' ? '📰' : '🆕'} <strong>{item.title}</strong></button>
                {why && <span className="muted"> — {why}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="news-bar">
        <div className="seg" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`seg-btn ${tab === t.id ? 'on' : ''}`} title={t.hint}
            onClick={() => set('tab', t.id)}>{t.label}{data?.counts[t.id] ? <span className="muted small"> {data.counts[t.id]}</span> : null}</button>
        ))}
        </div>
        {tab !== 'news' && tab !== 'product' && (
          <div className="seg" role="group" aria-label="Sort">
            {SORTS.map((o) => (
              <button key={o.id} className={`seg-btn ${sort === o.id ? 'on' : ''}`} title={o.hint}
                onClick={() => set('sort', o.id === 'newest' ? '' : o.id)}>{o.label}</button>
            ))}
          </div>
        )}
        <input className="news-search" type="search" placeholder="Search titles, groups, channels…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {tab === 'release' && (
        <div className="chips">
          {CATEGORIES.map(([id, label]) => (
            <button key={id} className={`chip ${category === id ? 'on' : ''}`} onClick={() => set('category', id)}>{label}</button>
          ))}
        </div>
      )}
      {st && Object.keys(st.errors).length > 0 && (
        <p className="muted small">Couldn't reach {Object.keys(st.errors).join(', ')} this time — it's tried again on the next check.</p>
      )}

      {tab !== 'news' && sort !== 'newest' && (
        <p className="muted small">{sort === 'popular'
          ? '🔥 Ranked by what each site shows: CSDb downloads, comments, votes and rating · itch.io ratings · YouTube views and likes. Each item is compared with recent items from the same site; brand-new releases climb as their numbers come in (updated every few hours).'
          : '📈 Popular for its age — a day-old release with 300 downloads beats a month-old one with 500.'}</p>
      )}
      {!data ? <Spinner /> : items.length === 0 ? (
        <Empty>{tab === 'product' ? 'No new hardware yet — the makers\' shops are checked daily (the first look only notes what they already sell).' : st?.checkedAt ? 'Nothing here yet.' : 'The first check is on its way — or press ↻ Check now.'}</Empty>
      ) : tab === 'video' ? (
        <div className="news-grid videos">
          {items.map((v) => (
            <button key={v.id} className="video-card" onClick={() => setWatching(v)} title={v.summary ?? v.title}>
              <span className="thumb">{v.image && <img src={v.image} alt="" loading="lazy" referrerPolicy="no-referrer" />}<span className="play">▶</span>
                {fresh(v) && <span className="badge-new">NEW</span>}<PopBadge item={v} sort={sort} /></span>
              <strong>{v.title}</strong>
              <span className="muted small">{v.author} · {ago(v.publishedAt)}</span>
              <StatLine item={v} />
            </button>
          ))}
        </div>
      ) : tab === 'product' ? (
        <ProductGrid items={items} fresh={fresh} />
      ) : tab === 'news' ? (
        <div className="news-list">
          {items.map((n) => (
            <article key={n.id} className="news-article">
              {n.image && <img src={n.image} alt="" loading="lazy" referrerPolicy="no-referrer" />}
              <div>
                <h3><a href={n.url} target="_blank" rel="noopener noreferrer">{n.title}</a> {fresh(n) && <span className="badge-new">NEW</span>}</h3>
                <p className="muted small">{n.sourceLabel} · {ago(n.publishedAt)}</p>
                {n.summary && <p>{n.summary}</p>}
                <a className="btn btn-ghost btn-sm" href={n.url} target="_blank" rel="noopener noreferrer">Read on {n.sourceLabel} ↗</a>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="news-grid releases">
          {items.map((r) => (
            <div key={r.id} className="release-card">
              <div className="thumb">
                {r.image ? <img src={r.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="no-art">{r.category === 'music' ? '🎵' : r.category === 'demo' ? '✨' : '🕹'}</span>}
                {fresh(r) && <span className="badge-new">NEW</span>}
                <PopBadge item={r} sort={sort} />
              </div>
              <strong title={r.title}>{r.title}</strong>
              <span className="muted small">{[r.releaseType, r.author].filter(Boolean).join(' · ')}</span>
              <span className="muted small">{r.sourceLabel} · {ago(r.publishedAt)}</span>
              <StatLine item={r} />
              <div className="release-actions">
                {r.playable ? (
                  <PlayChoice busy={busy === r.id} onC64={() => play(r, 'c64')} onBrowser={() => play(r, 'browser')} />
                ) : r.source === 'itch' ? (
                  <a className="btn btn-sm" href={r.url} target="_blank" rel="noopener noreferrer" title="Play, download or support the developer on itch.io (new tab)">▶ On itch.io ↗</a>
                ) : null}
                <a className="btn btn-ghost btn-sm" href={r.url} target="_blank" rel="noopener noreferrer">{r.source === 'csdb' ? 'CSDb ↗' : 'Page ↗'}</a>
                {r.gameId && <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/games/${r.gameId}`)} title="In your library">▤</button>}
              </div>
            </div>
          ))}
        </div>
      )}

      {watching && <VideoPlayer item={watching} onClose={() => setWatching(null)} />}
    </div>
  )
}

/** 🛒 New products in the makers' shops (opens the seller's page). */
export function ProductGrid({ items, fresh }: { items: NewsItem[]; fresh?: (i: NewsItem) => boolean }) {
  return (
    <div className="news-grid releases">
      {items.map((p) => (
        <a key={p.id} className="release-card product-card" href={p.url} target="_blank" rel="noopener noreferrer sponsored">
          <div className="thumb">
            {p.image ? <img src={p.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="no-art">🛒</span>}
            {fresh?.(p) && <span className="badge-new">NEW</span>}
          </div>
          <strong title={p.title}>{p.title}</strong>
          <span className="muted small">{[p.releaseType, p.author].filter(Boolean).join(' · ')}</span>
          <span className="muted small">{ago(p.publishedAt)}</span>
          <span className="btn btn-sm">View at {p.author} ↗</span>
        </a>
      ))}
    </div>
  )
}

/** 🎬 Watch a video right here (YouTube's privacy-enhanced player: no cookies until you press play). */
export function VideoPlayer({ item, onClose }: { item: NewsItem; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  if (!item.videoId) return null
  return (
    <div className="video-overlay" role="dialog" aria-label={item.title} onClick={onClose}>
      <div className="video-box" onClick={(e) => e.stopPropagation()}>
        <header>
          <strong>{item.title}</strong>
          <span className="row-actions">
            <a className="btn btn-ghost btn-sm" href={item.url} target="_blank" rel="noopener noreferrer">YouTube ↗</a>
            <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
          </span>
        </header>
        <div className="video-frame">
          <iframe src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(item.videoId)}?autoplay=1&rel=0`} title={item.title}
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
        </div>
        <p className="muted small">{item.author} · {ago(item.publishedAt)}{item.summary ? ` — ${item.summary}` : ''}</p>
      </div>
    </div>
  )
}
