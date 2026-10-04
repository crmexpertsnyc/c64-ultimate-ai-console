import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../services/api'
import type { SmartStep } from '../services/api'
import { currentProfile } from '../services/profile'

interface Guest { id: string; name: string; state: string; ping: number | null }
interface HostPlayer {
  stream(): MediaStream | null
  rtc(config: RTCConfiguration): RTCPeerConnection
  remoteJoy(st: Record<string, boolean>): void
  playStep(s: SmartStep): Promise<boolean>
}

const KEYS: Record<string, SmartStep> = {
  RETURN: { key: 'RETURN' }, SPACE: { key: 'SPACE' }, 'RUN/STOP': { key: 'RUN/STOP' },
  F1: { key: 'F1', code: 'F1', keyCode: 112 }, F3: { key: 'F3', code: 'F3', keyCode: 114 },
  F5: { key: 'F5', code: 'F5', keyCode: 116 }, F7: { key: 'F7', code: 'F7', keyCode: 118 },
  Y: { key: 'y', code: 'KeyY', keyCode: 89 }, N: { key: 'n', code: 'KeyN', keyCode: 78 },
  '1': { key: '1', code: 'Digit1', keyCode: 49 }, '2': { key: '2', code: 'Digit2', keyCode: 50 },
}

/**
 * 👥 Netplay host: open a co-op room for the game running here. A friend opens the link (same Wi-Fi, or anywhere
 * via Tailscale), sees and hears the game, and plays player 2 — their joystick comes back over WebRTC.
 */
export function NetplayHost({ gameId, port, player, onClose }: {
  gameId: number
  port: number
  player: () => HostPlayer | null | undefined
  onClose: () => void
}) {
  const [room, setRoom] = useState<{ code: string; link: string } | null>(null)
  const [guests, setGuests] = useState<Guest[]>([])
  const [error, setError] = useState<string | null>(null)
  const ws = useRef<WebSocket | null>(null)
  const peers = useRef(new Map<string, RTCPeerConnection>())
  const stun = useRef<string[]>([])

  const setGuest = (id: string, patch: Partial<Guest>) => setGuests((gs) => gs.map((g) => (g.id === id ? { ...g, ...patch } : g)))

  useEffect(() => {
    let closed = false
    const peerMap = peers.current
    ;(async () => {
      try {
        const r = await api.netplayRoom(gameId)
        stun.current = r.stun
        const link = `${window.location.origin}/netplay/${r.code}`
        setRoom({ code: r.code, link })
        const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const sock = new WebSocket(`${proto}://${window.location.host}/ws/netplay/${r.code}?role=host&profile=${currentProfile()}`)
        ws.current = sock
        sock.onmessage = (ev) => {
          const msg = JSON.parse(ev.data)
          if (msg.type === 'guest-joined') connect(msg.guestId, msg.name)
          else if (msg.type === 'guest-left') drop(msg.guestId)
          else if (msg.type === 'answer') peers.current.get(msg.from)?.setRemoteDescription(msg.data).catch(() => {})
          else if (msg.type === 'ice') peers.current.get(msg.from)?.addIceCandidate(msg.data).catch(() => {})
        }
        sock.onclose = () => { if (!closed) setError('The co-op room closed.') }
      } catch (e) {
        setError(errorMessage(e))
      }
    })()
    return () => {
      closed = true
      ws.current?.close()
      for (const pc of peerMap.values()) pc.close()
      peerMap.clear()
      try { player()?.remoteJoy({}) } catch { /* gone */ }
    }
  }, [gameId]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = (msg: unknown) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg)) }

  const drop = (id: string) => {
    peers.current.get(id)?.close()
    peers.current.delete(id)
    setGuests((gs) => gs.filter((g) => g.id !== id))
    try { player()?.remoteJoy({}) } catch { /* gone */ } // never leave a direction held
  }

  const connect = async (id: string, name: string) => {
    setGuests((gs) => [...gs.filter((g) => g.id !== id), { id, name, state: 'connecting…', ping: null }])
    const stream = player()?.stream()
    if (!stream) { setGuest(id, { state: 'the game is not running yet' }); return }
    // (created by the emulator's own document, where the canvas is — see play.html rtc())
    const pc = player()!.rtc({ iceServers: stun.current.map((urls) => ({ urls })) })
    peers.current.set(id, pc)
    ;(window as unknown as { __netplayPcs?: Map<string, RTCPeerConnection> }).__netplayPcs = peers.current // (diagnostics)
    for (const t of stream.getTracks()) pc.addTrack(t, stream)
    const dc = pc.createDataChannel('input', { ordered: false, maxRetransmits: 0 })
    dc.onmessage = (ev) => {
      let m: { j?: Record<string, boolean>; k?: string; ping?: number; rtt?: number }
      try { m = JSON.parse(ev.data) } catch { return }
      if (m.j) player()?.remoteJoy(m.j)
      if (m.k && KEYS[m.k]) player()?.playStep(KEYS[m.k])
      if (m.ping) dc.send(JSON.stringify({ pong: m.ping }))
      if (typeof m.rtt === 'number') setGuest(id, { ping: Math.round(m.rtt) })
    }
    dc.onopen = () => setGuest(id, { state: `playing as player 2 (port ${port === 1 ? 2 : 1})` })
    dc.onclose = () => { try { player()?.remoteJoy({}) } catch { /* gone */ } }
    pc.onicecandidate = (e) => { if (e.candidate) send({ type: 'ice', to: id, data: e.candidate }) }
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed') setGuest(id, { state: 'could not connect (network) — same Wi-Fi or Tailscale works best' })
      if (pc.connectionState === 'disconnected') setGuest(id, { state: 'connection lost…' })
    }
    // Picture quality: favour smoothness over sharpness on slow links.
    for (const s of pc.getSenders()) {
      if (s.track?.kind === 'video') {
        const p = s.getParameters()
        p.encodings = [{ ...(p.encodings?.[0] ?? {}), maxBitrate: 2_500_000, maxFramerate: 50 }]
        s.setParameters(p).catch(() => {})
      }
    }
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    send({ type: 'offer', to: id, data: pc.localDescription })
  }

  const copy = () => { if (room) navigator.clipboard?.writeText(room.link).catch(() => {}) }

  return (
    <div className="emu-panel" role="dialog" aria-label="Play together">
      <header><strong>👥 Play together</strong><button className="btn btn-ghost btn-sm" onClick={onClose} title="End the co-op session">End</button></header>
      {error && <p className="error-text small">{error}</p>}
      {!room && !error && <p className="muted small">Opening a room…</p>}
      {room && (
        <>
          <p className="small">Your friend opens this link (or scans it) and plays <b>player 2</b> — joystick port {port === 1 ? 2 : 1}:</p>
          <img className="netplay-qr" src={`/api/access/qr.svg?url=${encodeURIComponent(room.link)}`} alt="QR code for the co-op link" />
          <div className="issue-row"><code className="small">{room.link}</code><button className="btn btn-sm" onClick={copy}>Copy</button></div>
          <p className="muted small">Room code <b>{room.code}</b>. The game runs here; your friend sees and hears it and sends their joystick back.
            Works best on the same Wi-Fi or over Tailscale.</p>
          {guests.length === 0 ? <p className="muted small">Waiting for a friend to join…</p> : (
            <ul className="small">{guests.map((g) => <li key={g.id}><b>{g.name}</b> — {g.state}{g.ping !== null ? ` · ${g.ping} ms` : ''}</li>)}</ul>
          )}
        </>
      )}
    </div>
  )
}
