import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../services/api'
import type { ScreenSample } from '../services/startAnalyzer'

export interface CopilotReply {
  room: string | null
  exits: string[]
  inventory: string[]
  goal: string | null
  suggestions: { command: string; why: string }[]
  note: string | null
  map: Record<string, string[]>
}

const notesKey = (gameId: number) => `c64.copilot.${gameId}`

/**
 * 🧭 Text adventure co-pilot. Collects the adventure's text as it appears on the C64 screen, and on request
 * works out the room, exits and inventory, keeps a map, and suggests the next commands — click one to type
 * it on the C64 (with RETURN).
 */
export function Copilot({ gameId, screen, type, onClose }: {
  gameId: number
  screen: () => ScreenSample | null
  type: (text: string) => Promise<boolean>
  onClose: () => void
}) {
  const [reply, setReply] = useState<CopilotReply | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const screenRef = useRef(screen)
  screenRef.current = screen
  const lines = useRef<string[]>([])
  const lastRows = useRef<string[]>([])
  const notes = useRef<{ map: Record<string, string[]>; inventory: string[] }>((() => {
    try { return JSON.parse(localStorage.getItem(notesKey(gameId)) || '') } catch { return { map: {}, inventory: [] } }
  })())

  // Transcript: every new line of text the adventure prints (the screen scrolls; keep what appeared).
  useEffect(() => {
    const t = window.setInterval(() => {
      const sc = screenRef.current()
      if (!sc || sc.mode !== 'text') return
      const rows = sc.rows.filter(Boolean)
      const before = new Set(lastRows.current)
      for (const r of rows) if (!before.has(r) && lines.current[lines.current.length - 1] !== r) lines.current.push(r)
      if (lines.current.length > 160) lines.current.splice(0, lines.current.length - 160)
      lastRows.current = rows
    }, 1200)
    return () => window.clearInterval(t)
  }, [])

  const think = async () => {
    setBusy(true)
    setError(null)
    const sc = screen()
    const text = lines.current.length ? lines.current.join('\n') : (sc?.rows.filter(Boolean).join('\n') ?? '')
    try {
      const r = await api.copilot(gameId, text, notes.current)
      notes.current = { map: r.map, inventory: r.inventory.length ? r.inventory : notes.current.inventory }
      try { localStorage.setItem(notesKey(gameId), JSON.stringify(notes.current)) } catch { /* ignore */ }
      setReply(r)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const run = async (cmd: string) => {
    await type(cmd + '\n')
    window.setTimeout(think, 2500) // read the answer, then suggest again
  }
  const forget = () => {
    notes.current = { map: {}, inventory: [] }
    lines.current = []
    try { localStorage.removeItem(notesKey(gameId)) } catch { /* ignore */ }
    setReply(null)
  }

  const mapEntries = Object.entries(reply?.map ?? notes.current.map)
  return (
    <div className="emu-panel" role="dialog" aria-label="Text adventure co-pilot">
      <header><strong>🧭 Co-pilot</strong><button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button></header>
      {!reply && <p className="muted small">I read the adventure's text as it appears, keep track of where you are and what you carry, and suggest what to type next.</p>}
      {reply && (
        <>
          {reply.room && <p><strong>📍 {reply.room}</strong>{reply.exits.length > 0 && <span className="muted small"> · exits {reply.exits.join(' ')}</span>}</p>}
          {reply.goal && <p className="small">🎯 {reply.goal}</p>}
          {reply.inventory.length > 0 && <p className="small">🎒 {reply.inventory.join(', ')}</p>}
          <div className="copilot-cmds">
            {reply.suggestions.map((s) => (
              <button key={s.command} className="btn btn-sm" onClick={() => run(s.command)} title={s.why}>⌨ {s.command}</button>
            ))}
          </div>
          {reply.suggestions.length > 0 && <p className="muted small">Click to type it on the C64. {reply.suggestions[0]?.why}</p>}
          {reply.note && <p className="small">💬 {reply.note}</p>}
        </>
      )}
      {mapEntries.length > 1 && (
        <details className="small"><summary>🗺 Map ({mapEntries.length} places)</summary>
          <ul>{mapEntries.map(([room, exits]) => <li key={room}>{room}{exits.length ? ` → ${exits.join(' ')}` : ''}</li>)}</ul>
        </details>
      )}
      {error && <p className="error-text small">{error}</p>}
      <div className="row-actions">
        <button className="btn btn-primary btn-sm" onClick={think} disabled={busy}>{busy ? 'Thinking…' : reply ? '↻ What now?' : '🧭 Where am I? What next?'}</button>
        {mapEntries.length > 0 && <button className="btn btn-ghost btn-sm" onClick={forget} title="Start the notes over (new game)">Forget notes</button>}
      </div>
    </div>
  )
}
