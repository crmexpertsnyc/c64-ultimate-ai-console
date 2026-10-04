import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { errorMessage } from '../services/api'
import { bbsApi, connectionText } from '../services/bbsApi'
import type { Bbs } from '../services/bbsApi'
import { COLS, PETSCII_KEYS as K, PetsciiScreen, ROWS, decodeCp437, encodeCp437, keyToPetscii, textToPetscii } from '../services/petscii'
import { copyText } from './BbsPage'
import './bbs.css'

/**
 * 📟 Browser terminal for one approved board. Bytes travel through the console's relay (/ws/bbs/{id}); the console
 * never sees a host typed in the browser. PETSCII is drawn on a canvas, ANSI by xterm.js — never as HTML.
 */

type Mode = 'petscii' | 'ansi'
type State = 'idle' | 'connecting' | 'connected' | 'closed' | 'error'

const PETSCII_PAD: { label: string; bytes: number[]; title?: string }[] = [
  { label: 'RETURN', bytes: [K.RETURN] }, { label: 'DEL', bytes: [K.DEL] }, { label: 'INST', bytes: [K.INST] },
  { label: '↑', bytes: [K.UP], title: 'Cursor up' }, { label: '↓', bytes: [K.DOWN], title: 'Cursor down' },
  { label: '←', bytes: [K.LEFT], title: 'Cursor left' }, { label: '→', bytes: [K.RIGHT], title: 'Cursor right' },
  { label: 'HOME', bytes: [K.HOME] }, { label: 'CLR', bytes: [K.CLR] }, { label: 'RUN/STOP', bytes: [K.STOP] },
  { label: 'F1', bytes: [K.F1] }, { label: 'F3', bytes: [K.F3] }, { label: 'F5', bytes: [K.F5] }, { label: 'F7', bytes: [K.F7] },
  { label: 'F2', bytes: [K.F2] }, { label: 'F4', bytes: [K.F4] }, { label: 'F6', bytes: [K.F6] }, { label: 'F8', bytes: [K.F8] },
  { label: '⬅ (back arrow)', bytes: [K.LEFT_ARROW], title: 'The C64 ← key — many boards use it to go back' },
  { label: '£', bytes: [K.POUND] }, { label: '↑ (up arrow)', bytes: [K.UP_ARROW] },
  { label: 'RVS ON', bytes: [K.RVS_ON] }, { label: 'RVS OFF', bytes: [K.RVS_OFF] },
]
const ANSI_PAD: { label: string; text: string }[] = [
  { label: 'Enter', text: '\r' }, { label: 'Backspace', text: '\x08' }, { label: 'Esc', text: '\x1b' }, { label: 'Tab', text: '\t' },
  { label: '↑', text: '\x1b[A' }, { label: '↓', text: '\x1b[B' }, { label: '←', text: '\x1b[D' }, { label: '→', text: '\x1b[C' },
  { label: 'Space', text: ' ' }, { label: 'Y', text: 'Y' }, { label: 'N', text: 'N' }, { label: 'Q', text: 'Q' }, { label: 'Ctrl+C', text: '\x03' },
]

export function BbsTerminal() {
  const { id } = useParams()
  const boardId = Number(id)
  const toast = useToast()
  const [board, setBoard] = useState<Bbs | null>(null)
  const [loadError, setLoadError] = useState('')
  const [mode, setMode] = useState<Mode>('ansi')
  const [state, setState] = useState<State>('idle')
  const [message, setMessage] = useState('')
  const [pad, setPad] = useState(false)
  const [scale, setScale] = useState(0)             // PETSCII: 0 = fit the width; ANSI uses it as font size steps
  // ANSI is 80 columns: start with a text size that fits the screen (phones), up to 15px
  const [fontSize, setFontSize] = useState(() => Math.max(7, Math.min(15, Math.floor((window.innerWidth - 48) / 49))))

  const ws = useRef<WebSocket | null>(null)
  const screen = useRef(new PetsciiScreen())
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const wrap = useRef<HTMLDivElement | null>(null)
  const input = useRef<HTMLTextAreaElement | null>(null)
  const xtermHost = useRef<HTMLDivElement | null>(null)
  const xterm = useRef<Terminal | null>(null)
  const [fitScale, setFitScale] = useState(2)

  useEffect(() => {
    bbsApi.get(boardId).then((b) => {
      setBoard(b)
      // start in the mode the listing points to; the toolbar can change it for this session
      setMode(b.petscii !== 'unknown' && b.ansi !== 'confirmed' ? 'petscii' : 'ansi')
    }).catch((e) => setLoadError(errorMessage(e)))
  }, [boardId])

  // ---------------------------------------------------------- sending
  const sendBytes = useCallback((bytes: number[] | Uint8Array) => {
    const s = ws.current
    if (!s || s.readyState !== WebSocket.OPEN || !bytes.length) return
    s.send(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes))
  }, [])

  // ---------------------------------------------------------- PETSCII drawing
  useEffect(() => {
    if (mode !== 'petscii') return
    const measure = () => {
      const w = wrap.current?.clientWidth ?? 640
      setFitScale(Math.max(2, Math.min(4, Math.ceil((w - 24) / (COLS * 8)))))   // drawn sharp, then sized to the width by CSS
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [mode])
  const effScale = scale || fitScale

  useEffect(() => {
    if (mode !== 'petscii') return
    let raf = 0
    let blink = true
    let last = 0
    const ctx = canvas.current?.getContext('2d')
    if (!ctx) return
    screen.current.dirty = true
    document.fonts?.load(`${8 * effScale}px unscii8`).then(() => { screen.current.dirty = true }).catch(() => {})
    const tick = (t: number) => {
      if (t - last > 500) { blink = !blink; last = t; screen.current.dirty = true }
      if (screen.current.dirty) {
        screen.current.dirty = false
        screen.current.draw(ctx, effScale, blink && state === 'connected')
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [mode, effScale, state, board?.id])

  // ---------------------------------------------------------- ANSI terminal
  useEffect(() => {
    if (mode !== 'ansi' || !xtermHost.current) return
    const term = new Terminal({
      cols: 80, rows: 25, scrollback: 1000, convertEol: false, cursorBlink: true, fontSize,
      fontFamily: "'Cascadia Mono', Consolas, 'DejaVu Sans Mono', Menlo, monospace",
      theme: { background: '#000000', foreground: '#aaaaaa' }, allowProposedApi: false,
    })
    term.open(xtermHost.current)
    const sub = term.onData((d) => sendBytes(encodeCp437(d)))
    xterm.current = term
    return () => { sub.dispose(); term.dispose(); xterm.current = null }
    // runs again once the board has loaded (the host element exists from then on); font size changes apply below
  }, [mode, sendBytes, board?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (xterm.current) xterm.current.options.fontSize = fontSize }, [fontSize])

  // ---------------------------------------------------------- connection
  const disconnect = useCallback(() => {
    const s = ws.current
    ws.current = null
    if (s && s.readyState <= WebSocket.OPEN) {
      try { s.send(JSON.stringify({ type: 'close' })) } catch { /* closing anyway */ }
      s.close()
    }
    setState((st) => (st === 'connected' || st === 'connecting' ? 'closed' : st))
  }, [])

  const connect = useCallback(() => {
    if (!board) return
    disconnect()
    screen.current.reset()
    xterm.current?.reset()
    setMessage('')
    setState('connecting')
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const s = new WebSocket(`${proto}//${location.host}/ws/bbs/${board.id}?mode=${mode}&cols=${mode === 'petscii' ? COLS : 80}&rows=${mode === 'petscii' ? ROWS : 25}`)
    s.binaryType = 'arraybuffer'
    ws.current = s
    s.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          const m = JSON.parse(ev.data)
          if (m.type === 'status') {
            if (m.state === 'connected') { setState('connected'); setMessage(''); input.current?.focus(); xterm.current?.focus() }
            else if (m.state === 'disconnected') { setState('closed'); setMessage(String(m.detail ?? '')) }
          } else if (m.type === 'error') { setState('error'); setMessage(String(m.detail ?? 'Connection failed')) }
        } catch { /* ignore malformed control frames */ }
        return
      }
      const bytes = new Uint8Array(ev.data as ArrayBuffer)
      if (mode === 'petscii') screen.current.write(bytes)
      else xterm.current?.write(decodeCp437(bytes))
    }
    s.onclose = (ev) => {
      if (ws.current !== s) return
      ws.current = null
      setState((st) => (st === 'error' ? st : 'closed'))
      if (ev.code === 4401) setMessage('Sign in to the console first.')
      else if (ev.code === 4403) setMessage('The connection was refused (wrong page origin).')
      else setMessage((m) => m || 'Disconnected.')
    }
    s.onerror = () => setMessage((m) => m || "Couldn't reach the console's relay.")
  }, [board, mode, disconnect])

  useEffect(() => () => disconnect(), [disconnect])
  useEffect(() => { if (state === 'connected') disconnect() }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------- PETSCII keyboard
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key.length === 1 && !e.ctrlKey) return               // printable: handled by onInput (works with phone keyboards)
    const bytes = keyToPetscii(e)
    if (bytes) { e.preventDefault(); sendBytes(bytes) }
  }
  const onInput = (e: React.FormEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget
    const bytes = textToPetscii(ta.value)
    ta.value = ''
    if (bytes) sendBytes(bytes)
  }
  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    e.preventDefault()
    const bytes = textToPetscii(e.clipboardData.getData('text').slice(0, 2000))
    if (bytes) sendBytes(bytes)
  }

  const clear = () => { screen.current.reset(); xterm.current?.clear() }
  const copy = () => board && copyText(connectionText(board)).then(() => toast('Connection details copied', 'ok'), () => toast("Couldn't copy", 'error'))

  if (loadError) return <div className="page"><p>{loadError}</p><Link to="/bbs">← Back to the BBS directory</Link></div>
  if (!board) return <div className="page"><Spinner /></div>

  const live = state === 'connected' || state === 'connecting'
  return (
    <div className="page bbs-term-page">
      <header className="page-head bbs-term-head">
        <Link to="/bbs" className="btn btn-ghost btn-sm">← BBS</Link>
        <h1 className="bbs-term-title">{board.name}</h1>
        <span className={`bbs-state is-${state}`}>{{ idle: 'Not connected', connecting: 'Connecting…', connected: 'Connected', closed: 'Disconnected', error: 'Failed' }[state]}</span>
      </header>

      <div className="bbs-toolbar">
        {live
          ? <button className="btn btn-sm" onClick={disconnect}>⏏ Disconnect</button>
          : <button className="btn btn-primary btn-sm" onClick={connect} disabled={!board.approved}>▶ Connect</button>}
        <label className="small">Mode <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
          <option value="petscii">PETSCII (Commodore)</option><option value="ansi">ANSI / ASCII</option></select></label>
        {mode === 'petscii'
          ? <label className="small">Size <select value={scale} onChange={(e) => setScale(Number(e.target.value))}>
              <option value={0}>Fit</option><option value={1}>1×</option><option value={2}>2×</option><option value={3}>3×</option><option value={4}>4×</option></select></label>
          : <label className="small">Text <select value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))}>
              {[...new Set([7, 8, 9, 10, 12, 13, 15, 17, 20, 24, fontSize])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}px</option>)}</select></label>}
        <button className="btn btn-sm" onClick={clear}>Clear screen</button>
        <button className="btn btn-sm" onClick={() => setPad(!pad)} aria-pressed={pad}>⌨ Keys</button>
        <button className="btn btn-ghost btn-sm" onClick={copy}>📋 Details</button>
      </div>

      {message && <p className={`bbs-msg ${state === 'error' ? 'is-error' : ''}`}>{message}</p>}

      <div className="bbs-screen-wrap" ref={wrap}>
        {mode === 'petscii' ? (
          <div className={`bbs-petscii ${scale ? '' : 'is-fit'}`} style={{ borderColor: '#6c5eb5' }} onClick={() => input.current?.focus()}>
            <canvas ref={canvas} width={COLS * 8 * effScale} height={ROWS * 8 * effScale} aria-label="PETSCII terminal screen" />
            <textarea ref={input} className="bbs-input" aria-label="Type to the BBS" autoCapitalize="off" autoComplete="off" autoCorrect="off"
              spellCheck={false} onKeyDown={onKeyDown} onInput={onInput} onPaste={onPaste} />
          </div>
        ) : (
          <div className="bbs-ansi" ref={xtermHost} />
        )}
      </div>

      {pad && (
        <div className="bbs-pad" role="group" aria-label="On-screen keys">
          {mode === 'petscii'
            ? PETSCII_PAD.map((k) => <button key={k.label} className="btn btn-sm" title={k.title} onClick={() => { sendBytes(k.bytes); input.current?.focus() }}>{k.label}</button>)
            : ANSI_PAD.map((k) => <button key={k.label} className="btn btn-sm" onClick={() => { sendBytes(encodeCp437(k.text)); xterm.current?.focus() }}>{k.label}</button>)}
        </div>
      )}

      <p className="muted small">
        🔓 <b>Telnet is not encrypted.</b> Anything you type — including your BBS password — travels in plain text between the console and
        the board. Use a password you don't use anywhere else. Nothing you type or see is saved by the console.
        {mode === 'petscii' && <> Keyboard: letters, digits, RETURN, DEL (Backspace), cursor keys, Home (Shift+Home = CLR), Esc = RUN/STOP,
          F1–F8, Ctrl+1–8 colours, Ctrl+9/0 reverse on/off.</>}
      </p>
    </div>
  )
}
