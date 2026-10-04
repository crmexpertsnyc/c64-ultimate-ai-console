import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { get } from '../services/api'
import { Card } from './common'

/** ✨ Today's C64 moment: a game, a SID tune, a magazine from this month in history, a BBS — new every day. */
interface Moment {
  date: string
  game?: { id: number; title: string; coverUrl: string | null; year: number | null; publisher: string | null
    review: { magazine: string; score: string; date: string; coverUrl: string | null } | null }
  tune?: { title: string; composer: string }
  magazine?: { key: string; title: string; series: string; date: string; yearsAgo: number; month: string; year: number
    coverUrl: string; readerUrl: string }
  bbs?: { id: number; name: string; description: string | null; petscii: boolean; address: string; thumbUrl: string | null }
}

export function DailyMoment() {
  const [m, setM] = useState<Moment | null>(null)
  useEffect(() => { get<Moment>('/api/home/moment').then(setM).catch(() => {}) }, [])
  if (!m || !(m.game || m.tune || m.magazine || m.bbs)) return null
  const day = new Date(`${m.date}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  return (
    <Card title={<>✨ Today's C64 moment <span className="muted small">{day}</span></>} className="moment">
      <div className="moment-grid">
        {m.game && (
          <Link className="moment-item" to={`/games/${m.game.id}`}>
            <span className="moment-art">{m.game.coverUrl ? <img src={m.game.coverUrl} alt="" loading="lazy" /> : <span>🎮</span>}</span>
            <span className="moment-kind">🎮 Game of the day</span>
            <strong>{m.game.title}</strong>
            <span className="muted small">{[m.game.publisher, m.game.year].filter(Boolean).join(' · ')}
              {m.game.review && <> · {m.game.review.magazine} gave it <b>{m.game.review.score}</b></>}</span>
          </Link>
        )}
        {m.tune && (
          <Link className="moment-item" to={`/jukebox?q=${encodeURIComponent(m.tune.title)}`}>
            <span className="moment-art moment-sid"><span>🎵</span></span>
            <span className="moment-kind">🎵 Tune of the day</span>
            <strong>{m.tune.title}</strong>
            <span className="muted small">by {m.tune.composer} · play it on the real SID chip</span>
          </Link>
        )}
        {m.magazine && (
          <a className="moment-item" href={m.magazine.readerUrl} target="_blank" rel="noopener noreferrer">
            <span className="moment-art"><img src={m.magazine.coverUrl} alt="" loading="lazy" /></span>
            <span className="moment-kind">📚 {m.magazine.month} {m.magazine.year} — {m.magazine.yearsAgo} years ago</span>
            <strong>{m.magazine.title}</strong>
            <span className="muted small">Read the whole issue on the Internet Archive ↗</span>
          </a>
        )}
        {m.bbs && (
          <Link className="moment-item" to={`/bbs/${m.bbs.id}/terminal`}>
            <span className="moment-art">{m.bbs.thumbUrl ? <img src={m.bbs.thumbUrl} alt="" loading="lazy" /> : <span>📟</span>}</span>
            <span className="moment-kind">📟 Call a BBS today</span>
            <strong>{m.bbs.name}</strong>
            <span className="muted small">{m.bbs.petscii ? 'Commodore graphics · ' : ''}{m.bbs.description || m.bbs.address}</span>
          </Link>
        )}
      </div>
    </Card>
  )
}
