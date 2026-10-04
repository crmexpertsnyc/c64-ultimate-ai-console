import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { CatalogItem, CommandResult } from '../shared/types'
import { Modal } from './common'
import { AskAnswer } from './AskAnswer'
import type { AskResult } from './AskAnswer'

const EXAMPLES = ['Play Bruce Lee', 'Put disk 2 in', 'Press fire', "What's mounted?", 'What are the best C64 shoot ’em ups?', 'Reset the C64']

interface Exchange { id: number; text: string; result?: CommandResult; error?: string; ask?: boolean }

export function CommandBar({ context = {}, compact = false }: { context?: Record<string, unknown>; compact?: boolean }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<Exchange[]>([])
  const [confirm, setConfirm] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  // 💬 Ask: everything typed is a question (commands still work in the normal mode, where questions are
  // recognised automatically too).
  const [askMode, setAskMode] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const location = useLocation()
  const SpeechRec: any = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition

  const run = async (cmd: string, confirmed = false) => {
    const trimmed = cmd.trim()
    if (!trimmed) return
    const id = Date.now()
    setLog((l) => [{ id, text: trimmed, ask: askMode }, ...l].slice(0, 8))
    setBusy(true)
    try {
      const result: CommandResult = askMode
        ? await api.ask(trimmed).then((a) => ({ ok: true, message: a.answer, intent: null, data: { ask: a },
          needsConfirmation: false, candidates: [], onlineCandidates: [], interpretation: null }))
        : await api.command(trimmed, context, confirmed)
      setLog((l) => l.map((x) => (x.id === id ? { ...x, result } : x)))
      if (result.needsConfirmation) setConfirm(trimmed)
      setText('')
      // A game was launched: watch it load on the Display page.
      // "play X in the browser" → Browser Play; a game loaded on the C64 → the C64 Screen.
      if (result.ok && result.data?.navigate) navigate(result.data.navigate)
      else if (result.ok && result.data?.job) navigate('/stream')
    } catch (e) {
      setLog((l) => l.map((x) => (x.id === id ? { ...x, error: errorMessage(e) } : x)))
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const playOnline = async (item: CatalogItem) => {
    const id = Date.now()
    setLog((l) => [{ id, text: `Play ${item.name} (${item.source})` }, ...l].slice(0, 8))
    try {
      const r = await api.catalogPlay(item)
      setLog((l) => l.map((x) => (x.id === id ? { ...x, result: {
        ok: true, message: `Playing ${item.name} from Assembly64 (${item.source}).`, intent: null, data: r,
        needsConfirmation: false, candidates: [], onlineCandidates: [], interpretation: null,
      } } : x)))
      navigate('/stream')
    } catch (e) {
      setLog((l) => l.map((x) => (x.id === id ? { ...x, error: errorMessage(e) } : x)))
    }
  }

  const listen = () => {
    if (!SpeechRec) return
    const rec = new SpeechRec()
    rec.lang = 'en-US'
    rec.interimResults = false
    rec.onresult = (ev: any) => {
      const said = ev.results[0][0].transcript as string
      setText(said)
      run(said)
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    setListening(true)
    rec.start()
  }

  // The global "/" shortcut (in Layout) lands here, navigating Home first if needed.
  useEffect(() => {
    if ((location.state as { focusCommand?: boolean } | null)?.focusCommand) inputRef.current?.focus()
  }, [location.state])

  return (
    <div className={`commandbar ${compact ? 'commandbar-compact' : ''}`}>
      <form className="commandbar-form" onSubmit={(e) => { e.preventDefault(); run(text) }}>
        <button type="button" className={`btn btn-sm ask-toggle ${askMode ? 'on' : ''}`} onClick={() => { setAskMode((v) => !v); inputRef.current?.focus() }}
          title={askMode ? 'Ask mode: questions answered by your AI (with web search). Click for commands.' : 'Commands (questions are recognised too). Click for Ask mode.'}
          aria-pressed={askMode}>{askMode ? '💬 Ask' : '▶ Do'}</button>
        <input
          ref={inputRef}
          data-command-input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={askMode ? 'Ask anything about the C64…  e.g. “what are the best Epyx games?”' : 'Tell your C64…  e.g. “play Bruce Lee”, “play it in the browser”, or ask “what are the best racing games?”'}
          aria-label="Command"
          maxLength={300}
          autoComplete="off"
        />
        {SpeechRec && (
          <button type="button" className={`btn btn-ghost mic ${listening ? 'mic-on' : ''}`} onClick={listen}
            title="Speak a command (browser speech recognition)" aria-label="Speak a command">🎙</button>
        )}
        <button className="btn btn-primary" disabled={busy || !text.trim()}>{busy ? '…' : 'Go'}</button>
      </form>
      {!compact && (
        <div className="chips">
          {EXAMPLES.map((ex) => <button key={ex} className="chip" onClick={() => run(ex)}>{ex}</button>)}
        </div>
      )}
      {log.length > 0 && (
        <ul className="exchanges">
          {log.map((x) => (
            <li key={x.id}>
              <div className="you">› {x.text}</div>
              {!x.result && !x.error && <div className="reply muted">{x.ask || /\?$|^(what|which|who|why|how|research|recommend|tell me)/i.test(x.text) ? '🔎 researching…' : 'working…'}</div>}
              {x.error && <div className="reply reply-bad">{x.error}</div>}
              {x.result && (
                <div className={`reply ${x.result.ok ? 'reply-ok' : 'reply-bad'}`}>
                  {x.result.data?.ask ? <AskAnswer result={x.result.data.ask as AskResult} /> : x.result.message}
                  {x.result.intent && (
                    <span className="intent-tag" title={JSON.stringify(x.result.intent)}>
                      {x.result.intent.intent}{x.result.intent.source === 'llm' ? ' · AI' : ''}
                    </span>
                  )}
                  {x.result.onlineCandidates?.length > 0 && (
                    <div className="chips">
                      {x.result.onlineCandidates.map((c) => (
                        <button key={`${c.category}-${c.id}`} className="chip chip-online" title={`${c.source} — play`}
                          onClick={() => playOnline(c)}>
                          ▶ {c.name}{c.group ? ` · ${c.group.replace(/_/g, ' ')}` : ''}{c.year ? ` (${c.year})` : ''}
                          <span className="muted"> · {c.source}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {x.result.candidates.length > 0 && (
                    <div className="chips">
                      {x.result.candidates.map((g) => (
                        <button key={g.id} className="chip" onClick={() => navigate(`/games/${g.id}`)}>
                          {g.title}{g.year ? ` (${g.year})` : ''}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <Modal open={!!confirm} title="Confirm" onClose={() => setConfirm(null)} footer={
        <>
          <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
          <button className="btn btn-danger" onClick={() => { const c = confirm; setConfirm(null); if (c) run(c, true) }}>Yes, do it</button>
        </>
      }>
        <p>“{confirm}” needs confirmation. This will switch the C64 Ultimate off.</p>
      </Modal>
    </div>
  )
}
