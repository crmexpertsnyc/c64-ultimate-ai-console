import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, errorMessage } from '../services/api'

type Joy = { u: boolean; d: boolean; l: boolean; r: boolean; f: boolean }
const NONE: Joy = { u: false, d: false, l: false, r: false, f: false }
const KEY_JOY: Record<string, keyof Joy> = { ArrowUp: 'u', ArrowDown: 'd', ArrowLeft: 'l', ArrowRight: 'r', Space: 'f', ControlLeft: 'f' }
const KEY_SEND: Record<string, string> = { Enter: 'RETURN', F1: 'F1', F3: 'F3', F5: 'F5', F7: 'F7', KeyY: 'Y', KeyN: 'N', Digit1: '1', Digit2: '2', Escape: 'RUN/STOP' }

/**
 * 👥 Co-op guest: watch the host's game and play player 2. Keyboard (arrows + Space), a gamepad or the touch
 * controls; Enter / F-keys / Y / N / 1 / 2 are sent as C64 keys.
 */
export function NetplayGuest() {
  const { code = '' } = useParams()
  const [info, setInfo] = useState<{ title: string; hostOnline: boolean; full: boolean; stun: string[] } | null>(null)
  const [name, setName] = useState(() => { try { return localStorage.getItem('c64.netplay.name') || '' } catch { return '' } })
  const [joined, setJoined] = useState(false)
  const [status, setStatus] = useState('')
  const [rtt, setRtt] = useState<number | null>(null)
  const [muted, setMuted] = useState(true)
  const video = useRef<HTMLVideoElement>(null)
  const dc = useRef<RTCDataChannel | null>(null)
  const joy = useRef<Joy>({ ...NONE })
  const touch = useRef<Joy>({ ...NONE })
  const keys = useRef<Joy>({ ...NONE })

  useEffect(() => { api.netplayInfo(code).then(setInfo).catch((e) => setStatus(errorMessage(e))) }, [code])

  const send = (m: unknown) => { if (dc.current?.readyState === 'open') dc.current.send(JSON.stringify(m)) }
  const push = () => {
    const pads = navigator.getGamepads ? (Array.from(navigator.getGamepads()).filter(Boolean) as Gamepad[]) : []
    const pad = { ...NONE }
    for (const p of pads) {
      const b = (i: number) => !!p.buttons[i]?.pressed
      pad.u ||= b(12) || (p.axes[1] ?? 0) < -0.5
      pad.d ||= b(13) || (p.axes[1] ?? 0) > 0.5
      pad.l ||= b(14) || (p.axes[0] ?? 0) < -0.5
      pad.r ||= b(15) || (p.axes[0] ?? 0) > 0.5
      pad.f ||= b(0) || b(1) || b(3)
    }
    const next: Joy = { u: false, d: false, l: false, r: false, f: false }
    for (const k of Object.keys(next) as (keyof Joy)[]) next[k] = keys.current[k] || touch.current[k] || pad[k]
    if (JSON.stringify(next) !== JSON.stringify(joy.current)) { joy.current = next; send({ j: next }) }
  }

  const join = () => {
    const nm = name.trim() || 'Player 2'
    try { localStorage.setItem('c64.netplay.name', nm) } catch { /* ignore */ }
    setJoined(true)
    setStatus('Waiting for the host…')
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/netplay/${code}?role=guest&name=${encodeURIComponent(nm)}`)
    let pc: RTCPeerConnection | null = null
    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'error') setStatus(msg.message)
      else if (msg.type === 'host-left') { setStatus('The host ended the session.'); pc?.close() }
      else if (msg.type === 'offer') {
        pc = new RTCPeerConnection({ iceServers: (info?.stun ?? []).map((urls) => ({ urls })) })
        ;(window as unknown as { __netplayPc?: RTCPeerConnection }).__netplayPc = pc // (diagnostics)
        pc.ontrack = (e) => { if (video.current && e.streams[0]) video.current.srcObject = e.streams[0] }
        pc.ondatachannel = (e) => {
          dc.current = e.channel
          e.channel.onopen = () => setStatus('')
          e.channel.onmessage = (m) => {
            const d = JSON.parse(m.data)
            if (d.pong) { const r = performance.now() - d.pong; setRtt(Math.round(r)); send({ rtt: r }) }
          }
        }
        pc.onicecandidate = (e) => { if (e.candidate) ws.send(JSON.stringify({ type: 'ice', data: e.candidate })) }
        pc.onconnectionstatechange = () => {
          if (pc?.connectionState === 'failed') setStatus('Could not connect — try on the same Wi-Fi as the host, or via Tailscale.')
        }
        await pc.setRemoteDescription(msg.data)
        const answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        ws.send(JSON.stringify({ type: 'answer', data: pc.localDescription }))
        setStatus('Connecting…')
      } else if (msg.type === 'ice') pc?.addIceCandidate(msg.data).catch(() => {})
    }
    ws.onclose = () => setStatus((s) => s || 'Disconnected.')
  }

  // input: keyboard, gamepad (polled), touch — sent on change; ping for the delay
  useEffect(() => {
    if (!joined) return
    const down = (e: KeyboardEvent) => {
      const j = KEY_JOY[e.code]
      if (j) { e.preventDefault(); keys.current = { ...keys.current, [j]: true }; push() }
      else if (KEY_SEND[e.code] && !e.repeat) { e.preventDefault(); send({ k: KEY_SEND[e.code] }) }
    }
    const up = (e: KeyboardEvent) => { const j = KEY_JOY[e.code]; if (j) { keys.current = { ...keys.current, [j]: false }; push() } }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    const pad = window.setInterval(push, 16)
    const ping = window.setInterval(() => send({ ping: performance.now() }), 2000)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.clearInterval(pad); window.clearInterval(ping) }
  }, [joined]) // eslint-disable-line react-hooks/exhaustive-deps

  const hold = (k: keyof Joy, on: boolean) => { touch.current = { ...touch.current, [k]: on }; push() }
  const tb = (k: keyof Joy, label: string, cls: string) => (
    <button className={`np-btn ${cls}`} onPointerDown={(e) => { e.preventDefault(); hold(k, true) }} onPointerUp={() => hold(k, false)}
      onPointerLeave={() => hold(k, false)} onContextMenu={(e) => e.preventDefault()}>{label}</button>
  )

  if (!joined) {
    return (
      <div className="np np-join">
        <h1>👥 Play together</h1>
        {info ? <p>Join <b>{info.title}</b> as player 2{info.hostOnline ? '' : ' (the host is not connected right now)'}.</p> : <p className="muted">{status || 'Looking up the room…'}</p>}
        {info?.full && <p className="error-text">The room is full.</p>}
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" maxLength={24} />
        <button className="btn btn-primary" disabled={!info || info.full} onClick={join}>▶ Join</button>
        <p className="muted small">You'll see and hear the host's game and play with your arrow keys + Space, a gamepad, or the touch controls.</p>
      </div>
    )
  }
  return (
    <div className="np">
      <header className="np-head">
        <strong>👥 {info?.title}</strong>
        <span className="muted small">player 2{rtt !== null ? ` · ${rtt} ms` : ''}</span>
        <button className="btn btn-sm" onClick={() => { setMuted(!muted); if (video.current) { video.current.muted = !muted; video.current.play().catch(() => {}) } }}>{muted ? '🔊 Tap for sound' : '🔇 Mute'}</button>
      </header>
      <video ref={video} className="np-video" autoPlay playsInline muted={muted} />
      {status && <p className="np-status">{status}</p>}
      <div className="np-touch">
        <div className="np-pad">{tb('u', '▲', 'u')}{tb('l', '◀', 'l')}{tb('r', '▶', 'r')}{tb('d', '▼', 'd')}</div>
        <div className="np-keys">
          <button className="np-btn" onClick={() => send({ k: 'RETURN' })}>RETURN</button>
          <button className="np-btn" onClick={() => send({ k: 'SPACE' })}>SPACE</button>
          {tb('f', 'FIRE', 'fire')}
        </div>
      </div>
      <p className="muted small np-help">Keyboard: arrows = joystick · Space or Ctrl = fire · Enter = RETURN · F1–F7, Y / N, 1 / 2 · Esc = RUN/STOP</p>
    </div>
  )
}
