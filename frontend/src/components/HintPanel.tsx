import { useState } from 'react'
import { api, errorMessage } from '../services/api'
import type { ScreenSample } from '../services/startAnalyzer'

const LEVEL_LABEL = ['', '💡 Nudge', '🧭 More help', '🔓 Solution']

/**
 * 💡 "I'm stuck": spoiler-free help from what is on the C64's screen right now — a nudge first, then more
 * help, and the full solution only when asked.
 */
export function HintPanel({ gameId, screen, onClose }: {
  gameId: number
  screen: () => ScreenSample | null
  onClose: () => void
}) {
  const [question, setQuestion] = useState('')
  const [hints, setHints] = useState<{ level: number; text: string; sources: { n: number; title: string; url: string }[] }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const next = Math.min(3, (hints[hints.length - 1]?.level ?? 0) + 1)

  const ask = async (level: number) => {
    if (level === 3 && !window.confirm('Show the full solution for this spot? (spoiler)')) return
    setBusy(true)
    setError(null)
    const sc = screen()
    const text = sc && sc.mode === 'text' ? [sc.staticText, sc.scrollText.slice(-300)].filter(Boolean).join(' / ') : ''
    try {
      const r = await api.hint(gameId, { screen: text, question, level, previous: hints.map((h) => h.text) })
      setHints([...hints, { level: r.level, text: r.hint, sources: r.sources }])
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="emu-panel" role="dialog" aria-label="Hints">
      <header><strong>💡 Stuck?</strong><button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button></header>
      <p className="muted small">Help for what's on screen right now — a nudge first, the solution only if you ask.</p>
      <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What are you stuck on? (optional)"
        onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter' && !busy) ask(next) }} maxLength={200} />
      {hints.map((h, i) => (
        <div key={i} className="emu-hint-item">
          <span className="muted small">{LEVEL_LABEL[h.level]}</span>
          <p>{h.text}</p>
          {h.sources.length > 0 && <p className="small">{h.sources.map((s) => <a key={s.n} href={s.url} target="_blank" rel="noreferrer">{s.title}</a>)}</p>}
        </div>
      ))}
      {error && <p className="error-text small">{error}</p>}
      <div className="row-actions">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => ask(next)}>
          {busy ? 'Thinking…' : hints.length ? (next === 3 ? '🔓 Show the solution' : '🧭 More help') : '💡 Give me a nudge'}</button>
        {hints.length > 0 && next < 3 && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => ask(3)}>🔓 Just the solution</button>}
      </div>
    </div>
  )
}
