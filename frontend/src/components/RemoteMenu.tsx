import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { GAMEPAD_EVENT } from '../hooks/useGamepad'

const COLS = 80
const ROWS = 25

// Key codes the Ultimate's Telnet menu understands (verified on firmware 1.1.0s2).
const KEYS: { label: string; seq: string; title: string; wide?: boolean }[] = [
  { label: 'F1', seq: '\x1bOP', title: 'Action menu' },
  { label: 'F3', seq: '\x1bOR', title: 'Page up' },
  { label: 'F5', seq: '\x1b[15~', title: 'Page down' },
  { label: 'F7', seq: '\x1b[18~', title: 'Help' },
  { label: 'F2', seq: '\x1bOQ', title: 'Advanced settings' },
  { label: 'F4', seq: '\x1bOS', title: 'System information' },
  { label: 'F6', seq: '\x1b[17~', title: 'Internet file search (Assembly64)' },
  { label: 'RUN/STOP', seq: '\x08', title: 'Back / leave menu', wide: true },
]
const ARROWS: { label: string; seq: string; cls: string }[] = [
  { label: '▲', seq: '\x1b[A', cls: 'up' }, { label: '◀', seq: '\x1b[D', cls: 'left' },
  { label: '▶', seq: '\x1b[C', cls: 'right' }, { label: '▼', seq: '\x1b[B', cls: 'down' },
]

/** Maps browser terminal input to what the Ultimate expects: Escape and Backspace act as RUN/STOP. */
function translate(data: string): string {
  if (data === '\x1b' || data === '\x7f') return '\x08'
  return data
}

export function RemoteMenu() {
  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [state, setState] = useState<'connecting' | 'open' | 'closed'>('connecting')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const term = new Terminal({
      cols: COLS, rows: ROWS, cursorBlink: false, convertEol: false, scrollback: 0,
      fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, monospace', fontSize: 15,
      theme: { background: '#0f0e1a', foreground: '#d8d4f0' },
    })
    termRef.current = term
    term.open(host)

    // Scale the font so the fixed 80×25 screen fills the available width.
    const fit = () => {
      const width = host.clientWidth
      if (!width) return
      const size = Math.max(9, Math.min(20, Math.floor(width / (COLS * 0.61))))
      if (term.options.fontSize !== size) term.options.fontSize = size
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(host)

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/telnet`)
    wsRef.current = ws
    setState('connecting')
    ws.onopen = () => { setState('open'); term.focus() }
    ws.onmessage = (ev) => term.write(typeof ev.data === 'string' ? ev.data : new Uint8Array(ev.data))
    ws.onclose = () => setState('closed')
    const sub = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(translate(d)) })

    return () => {
      sub.dispose()
      ro.disconnect()
      ws.close()
      term.dispose()
      termRef.current = null
      wsRef.current = null
    }
  }, [attempt])

  // A game controller drives the Ultimate menu while it is open.
  useEffect(() => {
    const map: Record<string, string> = {
      up: '\x1b[A', down: '\x1b[B', left: '\x1b[D', right: '\x1b[C', a: '\r', b: '\x08', x: '\x1bOP', y: '\x1b[18~',
    }
    const onPad = (e: Event) => {
      const seq = map[(e as CustomEvent).detail.action]
      const ws = wsRef.current
      if (!seq || !ws || ws.readyState !== WebSocket.OPEN) return
      e.preventDefault()
      ws.send(seq)
    }
    window.addEventListener(GAMEPAD_EVENT, onPad)
    return () => window.removeEventListener(GAMEPAD_EVENT, onPad)
  }, [])

  const send = (seq: string) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(seq)
    termRef.current?.focus()
  }

  return (
    <div className="remote-menu">
      <div className="remote-term" ref={hostRef} onClick={() => termRef.current?.focus()} />
      <div className="remote-keys">
        <div className="remote-fkeys">
          {KEYS.map((k) => (
            <button key={k.label} className={`key key-fn ${k.wide ? 'wide' : ''}`} title={k.title} disabled={state !== 'open'}
              onClick={() => send(k.seq)}>
              <span>{k.label}</span><span className="key-sub">{k.title.split(' (')[0]}</span>
            </button>
          ))}
        </div>
        <div className="remote-arrows">
          {ARROWS.map((a) => (
            <button key={a.cls} className={`key key-fn arrow-${a.cls}`} disabled={state !== 'open'} onClick={() => send(a.seq)}
              aria-label={a.cls}>{a.label}</button>
          ))}
          <button className="key key-fn enter" disabled={state !== 'open'} onClick={() => send('\r')}>RETURN</button>
        </div>
        <div className="muted small">
          {state === 'open' && 'Click the screen and use your keyboard: arrows or WASD move, Enter selects, Esc/Backspace = RUN/STOP (back). Info screens close with Enter.'}
          {state === 'connecting' && 'Connecting to the Ultimate…'}
          {state === 'closed' && (
            <>Disconnected. <button className="link" onClick={() => setAttempt((n) => n + 1)}>Reconnect</button></>
          )}
        </div>
      </div>
    </div>
  )
}
