import { useEffect, useState } from 'react'
import { api, errorMessage } from '../services/api'
import { Card, Spinner } from './common'
import { useToast } from './Toasts'

export interface Guide {
  summary: string
  start: string[]
  startKeys: string[]
  controls: { action: string; how: string }[]
  joystickPort: number | null
  players: string | null
  tips: string[]
  sources: { n: number; title: string; url: string }[]
  generatedAt: number
  webSearch: boolean
  model: string | null
}

/** 📖 How to play: researched once (web + AI), stored with the game. */
export function GuideCard({ gameId, initial, onGuide }: { gameId: number; initial?: Guide | null; onGuide?: (g: Guide) => void }) {
  const toast = useToast()
  const [guide, setGuide] = useState<Guide | null | undefined>(initial)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (initial !== undefined) { setGuide(initial); return }
    api.guide(gameId).then((r) => setGuide(r.guide)).catch(() => setGuide(null))
  }, [gameId, initial])

  const research = async () => {
    setBusy(true)
    try {
      const r = await api.makeGuide(gameId)
      setGuide(r.guide)
      onGuide?.(r.guide)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="📖 How to play" actions={guide && (
      <button className="btn btn-ghost btn-sm" disabled={busy} onClick={research} title="Research the guide again">↻</button>
    )}>
      {guide === undefined ? <Spinner /> : !guide ? (
        <div>
          <p className="muted small">How to start it, the controls, the joystick port and tips — researched on the web and summarised by your AI.</p>
          <button className="btn btn-primary" disabled={busy} onClick={research}>{busy ? '🔎 Researching…' : '📖 Find how to play'}</button>
        </div>
      ) : (
        <div className="guide">
          {guide.summary && <p>{guide.summary}</p>}
          {guide.start.length > 0 && (
            <>
              <h4>Getting started</h4>
              <ol>{guide.start.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </>
          )}
          {guide.controls.length > 0 && (
            <>
              <h4>Controls</h4>
              <table className="keys-table"><tbody>
                {guide.controls.map((c, i) => <tr key={i}><td>{c.action}</td><td>{c.how}</td></tr>)}
              </tbody></table>
            </>
          )}
          <p className="small">
            {guide.joystickPort ? <>🕹 Joystick <strong>port {guide.joystickPort}</strong> · </> : null}
            {guide.players ? <>👥 {guide.players} players</> : null}
          </p>
          {guide.tips.length > 0 && <ul className="small">{guide.tips.map((t, i) => <li key={i}>{t}</li>)}</ul>}
          {guide.sources.length > 0 && (
            <div className="ask-sources small">Sources: {guide.sources.map((s) => (
              <a key={s.n} href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
            ))}</div>
          )}
          <p className="muted small">{guide.webSearch ? '🔎 Web + ' : ''}🤖 {guide.model || 'AI'} · {new Date(guide.generatedAt * 1000).toLocaleDateString()} · may contain mistakes</p>
        </div>
      )}
    </Card>
  )
}
