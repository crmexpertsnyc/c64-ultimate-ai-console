import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, get } from '../services/api'
import type { Digest, NewsItem } from '../pages/NewsPage'
import type { RetroEvent } from '../services/eventsApi'
import { useLive } from '../hooks/useLive'
import { Card } from './common'

interface Unlock { title: string; icon: string; description: string; game: string; gameId: number; at: string | null; name: string; emoji: string; source: string }
interface Home {
  thisWeek: { digest: Digest | null; releases: NewsItem[]; news: NewsItem[]; videos: NewsItem[] }
  events: { items: RetroEvent[]; live: RetroEvent[]; homeCountry: string | null }
  achievements: Unlock[]
  hardware: { products: NewsItem[]; deals: NewsItem[] }
}
type Save = { gameId: number; title: string; coverUrl: string | null; savedAt: number | null; thumb: string | null }

const mon = (iso: string) => new Date(`${iso}T00:00`).toLocaleDateString([], { month: 'short' })
const day = (iso: string) => new Date(`${iso}T00:00`).getDate()
function ago(iso: string | number | null): string {
  if (!iso) return ''
  const d = (Date.now() - new Date(iso).getTime()) / 86400000
  return d < 1 ? 'today' : d < 2 ? 'yesterday' : d < 14 ? `${Math.floor(d)} days ago` : new Date(iso).toLocaleDateString()
}

/** ◉ The front page's hub: continue playing · this week in C64 · coming up near you · achievements · new hardware. */
export function HomeHub() {
  const navigate = useNavigate()
  const { news } = useLive()
  const [h, setH] = useState<Home | null>(null)
  const [saves, setSaves] = useState<Save[]>([])
  useEffect(() => { get<Home>('/api/home').then(setH).catch(() => {}) }, [news?.at])
  useEffect(() => {
    api.saves().then((r) => setSaves([...r.saves].sort((a, b) => (b.savedAt ?? 0) - (a.savedAt ?? 0)).slice(0, 6))).catch(() => {})
  }, [])
  if (!h) return null
  const tw = h.thisWeek
  const ev = h.events
  return (
    <div className="home-hub">
      {saves.length > 0 && (
        <Card title="▶ Continue playing" className="home-continue" actions={<Link className="btn btn-ghost btn-sm" to="/emulate">Browser Play →</Link>}>
          <div className="home-saves">
            {saves.map((s) => (
              <button key={s.gameId} className="home-save" onClick={() => navigate(`/emulate/${s.gameId}`)} title={`Resume ${s.title} where you left off`}>
                <span className="thumb">{s.thumb || s.coverUrl ? <img src={(s.thumb || s.coverUrl)!} alt="" loading="lazy" /> : <span>🎮</span>}<span className="play">▶</span></span>
                <strong>{s.title}</strong>
                <span className="muted small">{s.savedAt ? `saved ${ago(s.savedAt * (s.savedAt < 1e12 ? 1000 : 1))}` : ''}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      <div className="home-grid">
        <Card title="🆕 This week in C64" actions={<Link className="btn btn-ghost btn-sm" to="/news">All →</Link>}>
          {tw.digest && <p className="home-digest"><strong>🤖 {tw.digest.headline}</strong> <span className="muted small">{tw.digest.intro}</span></p>}
          {tw.releases.length > 0 && (
            <div className="home-releases">
              {tw.releases.map((r) => (
                <Link key={r.id} className="home-release" to={r.source === 'csdb' ? '/news?tab=release&sort=popular' : '/news?tab=release'} title={`${r.title} — ${r.releaseType ?? ''} ${r.author ?? ''}`}>
                  <span className="thumb">{r.image ? <img src={r.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span>🕹</span>}
                    {r.popularity !== null && r.popularity >= 75 && <span className="badge-pop hot">🔥</span>}</span>
                  <span className="small">{r.title}</span>
                </Link>
              ))}
            </div>
          )}
          <ul className="home-list">
            {tw.news.map((n) => <li key={n.id}><a href={n.url} target="_blank" rel="noopener noreferrer">📰 {n.title}</a> <span className="muted small">· {n.sourceLabel}</span></li>)}
            {tw.videos.map((v) => <li key={v.id}><Link to="/news?tab=video">🎬 {v.title}</Link> <span className="muted small">· {v.author}</span></li>)}
          </ul>
        </Card>

        <Card title={`📅 Coming up${ev.homeCountry ? ` in the ${ev.homeCountry}` : ''}`} actions={<Link className="btn btn-ghost btn-sm" to="/events">All events →</Link>}>
          {ev.live.map((e) => <p key={`live-${e.id}`} className="small"><span className="ev-live-badge">🔴 LIVE</span> <strong>{e.name}</strong> · {[e.city, e.country].filter(Boolean).join(', ')}</p>)}
          {ev.items.length ? (
            <ul className="home-events">
              {ev.items.map((e) => (
                <li key={e.id}>
                  <span className="home-date"><span>{mon(e.start)}</span><b>{day(e.start)}</b></span>
                  <span><strong>{e.name}</strong><br /><span className="muted small">{[e.city, e.region, e.home ? null : e.country].filter(Boolean).join(', ')}{e.type ? ` · ${e.type}` : ''}</span></span>
                  {e.url && <a className="btn btn-ghost btn-sm" href={e.url} target="_blank" rel="noopener noreferrer" title="Website">↗</a>}
                </li>
              ))}
            </ul>
          ) : <p className="muted small">No upcoming events found yet — they're checked daily.</p>}
        </Card>

        <Card title="🏆 Achievements" actions={<Link className="btn btn-ghost btn-sm" to="/library">Games →</Link>}>
          {h.achievements.length ? (
            <ul className="home-list">
              {h.achievements.map((a, i) => (
                <li key={i}><span className="home-ach">{a.icon}</span> <strong>{a.title}</strong> — <Link to={`/games/${a.gameId}`}>{a.game}</Link>
                  <span className="muted small"> · {a.emoji} {a.name}{a.source === 'c64' ? ' · 📺 C64 ✓' : ''} · {ago(a.at)}</span></li>
              ))}
            </ul>
          ) : <p className="muted small">Play a game with a score on screen and achievements unlock here — the whole family's.</p>}
        </Card>

        <Card title="🛒 New hardware & deals" actions={<Link className="btn btn-ghost btn-sm" to="/shop?tab=arrivals">Hardware →</Link>}>
          {h.hardware.deals.length > 0 && (
            <ul className="home-list">{h.hardware.deals.map((d) => <li key={d.id}><a href={d.url} target="_blank" rel="noopener noreferrer sponsored">{d.title}</a></li>)}</ul>
          )}
          {h.hardware.products.length ? (
            <ul className="home-list">
              {h.hardware.products.map((p) => (
                <li key={p.id}><a href={p.url} target="_blank" rel="noopener noreferrer sponsored">{p.title}</a>
                  <span className="muted small"> · {[p.releaseType, p.author].filter(Boolean).join(' · ')}</span></li>
              ))}
            </ul>
          ) : <p className="muted small">New products from the makers show up here — the shops are checked daily.{!h.hardware.deals.length && ' Add eBay keys in Settings for 💰 price alerts.'}</p>}
        </Card>
      </div>
    </div>
  )
}
