import { useEffect, useState } from 'react'
import { api, errorMessage } from '../services/api'
import { Card } from './common'
import { useToast } from './Toasts'

export interface Achievement {
  id: number; gameId: number | null; title: string; description: string; icon: string
  metric: string; target: number; source: 'ai' | 'builtin'; unlocked: boolean
  unlockedBy?: { profileId: number; name: string; emoji: string; at: string | null; source: string }[]
}
export interface GameAchievements {
  achievements: Achievement[]
  seen: Record<string, number>
  progress: { minutes: number; days: number }
  hasGameAchievements: boolean
  scores: { score: number; level: number | null; source: string; at: string | null; profileId: number; name: string; emoji: string }[]
}

/** 🏆 Achievements and the family high-score table for one game (scores are read from the game's own screen). */
export function AchievementsCard({ gameId }: { gameId: number }) {
  const toast = useToast()
  const [data, setData] = useState<GameAchievements | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => api.gameAchievements(gameId).then(setData).catch(() => {})
  useEffect(() => { load() }, [gameId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return null
  const make = async () => {
    setBusy(true)
    try {
      const r = await api.makeAchievements(gameId)
      toast(`🏆 ${r.achievements.length} new achievements for this game`, 'ok')
      load()
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  const unlocked = data.achievements.filter((a) => a.unlocked).length
  const canMake = Object.keys(data.seen).some((m) => ['score', 'level', 'lives', 'time'].includes(m))
  return (
    <Card title={`🏆 Achievements · ${unlocked}/${data.achievements.length}`} actions={
      <button className="btn btn-ghost btn-sm" onClick={make} disabled={busy || !canMake}
        title={canMake ? 'Ask the AI for achievements based on what this game shows (score, round…)' : 'Play the game for a moment first so its score or round has been seen on screen'}>
        {busy ? '🤖 Thinking…' : data.hasGameAchievements ? '🤖 ↻' : '🤖 Make achievements'}</button>
    }>
      <ul className="ach-list">
        {data.achievements.map((a) => (
          <li key={a.id} className={a.unlocked ? 'on' : ''}>
            <span className="ach-icon">{a.unlocked ? a.icon : '🔒'}</span>
            <span><strong>{a.title}</strong> <span className="muted small">{a.description}</span>
              {a.unlockedBy && a.unlockedBy.length > 0 && <span className="small"> · {a.unlockedBy.map((u) => `${u.emoji} ${u.name}`).join(', ')}</span>}</span>
          </li>
        ))}
      </ul>
      {!data.hasGameAchievements && <p className="muted small">{canMake ? 'This game shows a score — 🤖 can make achievements for it.' : 'Play it: once its score or round has been seen on screen, achievements can be made for it.'}</p>}
      {data.scores.length > 0 && (
        <>
          <h4>High scores</h4>
          <ol className="score-list">
            {data.scores.map((s, i) => (
              <li key={i}><span>{s.emoji} {s.name}</span><strong>{s.score.toLocaleString()}</strong>
                <span className="muted small">{s.level ? `round ${s.level} · ` : ''}{s.source === 'c64' ? '📺 C64 ✓' : '💻 browser'}{s.at ? ` · ${new Date(s.at).toLocaleDateString()}` : ''}</span></li>
            ))}
          </ol>
          <p className="muted small">Read from the game's own screen — never typed in. 📺 C64 scores are read by the console itself.</p>
        </>
      )}
    </Card>
  )
}
