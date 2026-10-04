/**
 * Live joystick channel (WS /ws/joystick) for gamepad passthrough: sends the whole joystick state
 * on every change. Lower latency than one HTTP request per press, and the server releases
 * everything this socket held if the page closes or the connection drops.
 */
let ws: WebSocket | null = null
let pending: string | null = null
let onError: (msg: string) => void = () => {}

export function setJoystickErrorHandler(fn: (msg: string) => void) {
  onError = fn
}

function open(): WebSocket {
  if (ws && ws.readyState <= WebSocket.OPEN) return ws
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const sock = new WebSocket(`${proto}://${location.host}/ws/joystick`)
  sock.onopen = () => {
    if (pending) sock.send(pending)
    pending = null
  }
  sock.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'error') onError(msg.detail)
    } catch { /* ignored */ }
  }
  sock.onclose = () => { if (ws === sock) ws = null }
  ws = sock
  return sock
}

export function sendJoystick(port: number, inputs: string[]) {
  const msg = JSON.stringify({ port, inputs })
  const sock = open()
  if (sock.readyState === WebSocket.OPEN) sock.send(msg)
  else pending = msg // only the latest state matters
}

export function closeJoystick() {
  ws?.close()
  ws = null
  pending = null
}
