import { useEffect, useState } from 'react'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import { Card, Dot } from './common'
import { useToast } from './Toasts'

/** Settings card for the ESP32 joystick bridge: address, discovery, live status and a wiring test. */
export function JoyBridgeCard() {
  const { status } = useLive()
  const toast = useToast()
  const bridge = status?.input?.bridge
  const [host, setHost] = useState('')
  const [busy, setBusy] = useState<'' | 'find' | 'save' | 'test'>('')
  const [found, setFound] = useState<{ host: string; name?: string | null; ports?: number[] }[] | null>(null)

  useEffect(() => { if (bridge) setHost(bridge.host) }, [bridge?.host]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (value: string) => {
    setBusy('save')
    try {
      await api.saveSettings({ JOYBRIDGE_HOST: value.trim() })
      setHost(value.trim())
      toast(value.trim() ? 'Joystick bridge saved — connecting…' : 'Joystick bridge removed', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy('')
    }
  }

  const find = async () => {
    setBusy('find')
    setFound(null)
    try {
      const r = await api.joybridgeDiscover()
      setFound(r.found)
      if (!r.found.length) toast('No joystick bridge answered on this network', 'error')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy('')
    }
  }

  const test = async () => {
    setBusy('test')
    try {
      const r = await api.joybridgeTest(status?.input?.joystickPort)
      toast(`Sent ${r.sent.join(' → ')} on port ${r.port}`, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy('')
    }
  }

  const online = !!bridge?.online
  return (
    <Card title="🕹 Joystick bridge (ESP32)">
      <div id="joybridge" />
      <p className="muted small">
        Your firmware can't take joystick input over the network, so a small ESP32 board wired into the C64's joystick
        port presses the joystick for you — from the on-screen joystick, your PC keyboard or a gamepad.
        Build guide: <code>docs/joystick-bridge.md</code>.
      </p>
      {bridge?.configured && (
        <dl className="kv">
          <dt>Status</dt>
          <dd><Dot tone={online ? 'ok' : 'bad'} /> {online ? `Online — ${bridge.name ?? 'bridge'}` : 'Not responding'}</dd>
          {online && <>
            <dt>Wired ports</dt><dd>{bridge.ports.length ? bridge.ports.map((p) => `Port ${p}`).join(', ') : 'none'}</dd>
            <dt>Network</dt><dd>{bridge.rttMs ?? '?'} ms round trip · Wi-Fi {bridge.rssi ?? '?'} dBm{bridge.rssi != null && bridge.rssi < -75 ? ' (weak — move it closer to the router)' : ''}</dd>
            <dt>Firmware</dt><dd>{bridge.firmware}</dd>
          </>}
        </dl>
      )}
      <div className="row-actions">
        <label className="field"><span>Bridge address</span>
          <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="e.g. 192.168.1.50 or c64-joybridge.local" /></label>
        <button className="btn btn-primary" disabled={busy !== '' || host.trim() === (bridge?.host ?? '')} onClick={() => save(host)}>Save</button>
        <button className="btn" disabled={busy !== ''} onClick={find}>{busy === 'find' ? 'Searching…' : '🔍 Find on network'}</button>
        {bridge?.configured && <button className="btn btn-ghost" disabled={busy !== ''} onClick={() => save('')}>Remove</button>}
      </div>
      {found && found.length > 0 && (
        <ul className="share-list">
          {found.map((f) => (
            <li key={f.host}>
              <button className="share-pick" onClick={() => save(f.host)}>
                <span aria-hidden>🕹</span><span className="mono">{f.host}</span>
                <span className="muted small">{f.name} · ports {(f.ports ?? []).join(', ') || 'none'} — click to use</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {online && (
        <div className="row-actions">
          <button className="btn" disabled={busy !== ''} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test wiring'}</button>
          <span className="muted small">Taps up, down, left, right, fire on port {status?.input?.joystickPort ?? 2}. Watch a joystick
            test program on the C64 (or the bridge's LED).</span>
        </div>
      )}
      <p className="muted small">No board yet? Try it with the software stand-in: <code>python -m app.ultimate.joybridge_fake</code> in
        the backend folder, then use address <code>127.0.0.1</code>.</p>
    </Card>
  )
}
