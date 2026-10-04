import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import { Card } from './common'
import { useToast } from './Toasts'

export interface Recap {
  stats: { minutes: number; plays: number; daysActive: number; searches: number; newGames: string[]
    games: { title: string; gameId: number | null; minutes: number; plays: number; coverUrl: string | null }[] }
  headline: string | null
  summary: string | null
  challenges: { game: string; gameId: number | null; challenge: string; tip: string; done: boolean }[]
  ai: boolean
  note: string | null
  generatedAt: number
}

/** 📊 Your week: play time, top games, a written recap and three challenges to tick off. */
export function YourWeek() {
  const navigate = useNavigate()
  const toast = useToast()
  const [r, setR] = useState<Recap | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.recap().then(setR).catch(() => {}) }, [])
  const refresh = async () => {
    setBusy(true)
    try { setR(await api.refreshRecap()) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const tick = async (i: number, done: boolean) => {
    try {
      setR(await api.recapChallenge(i, done))
      if (done) toast('🏆 Challenge complete!', 'ok')
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  if (!r) return null
  const st = r.stats
  const hours = st.minutes >= 60 ? `${Math.floor(st.minutes / 60)} h ${st.minutes % 60} min` : `${st.minutes} min`
  return (
    <Card title="📊 Your week" actions={<button className="btn btn-ghost btn-sm" onClick={refresh} disabled={busy} title="Recount and rewrite">{busy ? '…' : '↻'}</button>}>
      {r.headline && <h3 className="recap-headline">{r.headline}</h3>}
      {r.summary && <p>{r.summary}</p>}
      {st.games.length > 0 && (
        <div className="recap-stats">
          <div><strong>{hours}</strong><span className="muted small">played in the browser</span></div>
          <div><strong>{st.plays}</strong><span className="muted small">games started</span></div>
          <div><strong>{st.daysActive}</strong><span className="muted small">days active</span></div>
          <div><strong>{st.newGames.length}</strong><span className="muted small">new games tried</span></div>
        </div>
      )}
      {st.games.length > 0 && (
        <p className="small">Most played: {st.games.slice(0, 3).map((g, i) => (
          <span key={g.title}>{i ? ', ' : ''}{g.gameId ? <button className="linklike" onClick={() => navigate(`/games/${g.gameId}`)}>{g.title}</button> : g.title}{g.minutes ? ` (${g.minutes} min)` : ''}</span>
        ))}</p>
      )}
      {r.challenges.length > 0 && (
        <>
          <h4>🏆 This week's challenges</h4>
          <ul className="recap-challenges">
            {r.challenges.map((c, i) => (
              <li key={i} className={c.done ? 'done' : ''}>
                <label><input type="checkbox" checked={c.done} onChange={(e) => tick(i, e.target.checked)} />
                  <span><strong>{c.game}</strong>: {c.challenge}{c.tip && <span className="muted small"> — tip: {c.tip}</span>}</span></label>
                {c.gameId && <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/emulate/${c.gameId}`)}>▶ Play</button>}
              </li>
            ))}
          </ul>
        </>
      )}
      {r.note && <p className="muted small">{r.note}</p>}
    </Card>
  )
}
