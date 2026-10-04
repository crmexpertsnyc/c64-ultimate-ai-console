import { useEffect, useRef, useState } from 'react'
import { GearSuggestions } from '../components/GearSuggestions'
import { useLocation } from 'react-router-dom'
import { Card } from '../components/common'
import { useToast } from '../components/Toasts'
import { Joystick } from '../controllers/Joystick'
import { Keyboard } from '../controllers/Keyboard'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'

export function ControllerPage() {
  const { status, refreshStatus } = useLive()
  const toast = useToast()
  const location = useLocation()
  const kbdRef = useRef<HTMLDivElement>(null)
  const [text, setText] = useState('')
  const [pressReturn, setPressReturn] = useState(true)
  const mode = status?.inputMode ?? 'none'
  const port = status?.input?.joystickPort ?? 2
  const joystickOk = !!status?.input?.joystickSupported && !!status?.connected
  const viaBridge = status?.input?.joystickVia === 'bridge'

  useEffect(() => {
    if (location.hash === '#keyboard') kbdRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [location.hash])

  const setPort = async (p: number) => {
    try {
      await api.joystickPort(p)
      if (status) refreshStatus({ ...status, input: { ...status.input, joystickPort: p } })
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const typeText = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const r = await api.type(text, pressReturn)
      toast(`Typed ${r.typed} characters (${r.mode})`, 'ok')
      setText('')
    } catch (err) {
      toast(errorMessage(err), 'error')
    }
  }

  return (
    <div className="page">
      <div className="page-head"><h1>Virtual controller</h1></div>
      {mode === 'legacy' && (
        <div className="banner banner-warn">
          Legacy input mode: this firmware has no REST input API. Text is typed through the KERNAL keyboard buffer
          (only while BASIC/the KERNAL editor owns the keyboard). Holding keys, RUN/STOP and RESTORE are not available.
          {viaBridge ? ' The joystick works through your joystick bridge.' : ' Joystick input needs an ESP32 joystick bridge (Settings → Joystick bridge).'}
        </div>
      )}
      {mode === 'none' && status?.connected && (
        <div className="banner banner-bad">No input method is available on this device.</div>
      )}

      <div className="grid-2">
        <Card title="Joystick" actions={status?.input?.held.length ? <span className="badge badge-unverified">{status.input.held.length} held</span> : null}>
          {joystickOk ? (
            <Joystick port={port} enabled={joystickOk} onPortChange={setPort} singleFire={viaBridge}
              ports={viaBridge ? status?.input?.bridge?.ports ?? [2] : [1, 2]} />
          ) : (
            <div className="muted">Joystick injection is not supported by this firmware. Add an ESP32 joystick bridge
              (Settings → Joystick bridge) to control games from here.</div>
          )}
        </Card>
        <Card title="Type text">
          <form onSubmit={typeText} className="type-form">
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder='LOAD"*",8,1' maxLength={512}
              disabled={mode === 'none'} aria-label="Text to type" />
            <label className="toggle"><input type="checkbox" checked={pressReturn} onChange={(e) => setPressReturn(e.target.checked)} /> RETURN</label>
            <button className="btn btn-primary" disabled={!text || mode === 'none'}>Type</button>
          </form>
          <div className="chips">
            {['LOAD"*",8,1', 'LOAD"$",8', 'LIST', 'RUN', 'SYS 64738'].map((t) => (
              <button key={t} className="chip" onClick={() => setText(t)}>{t}</button>
            ))}
          </div>
          <p className="muted small">Lower-case letters are typed as unshifted keys (upper case on a stock C64).</p>
        </Card>
      </div>

      <div ref={kbdRef} id="keyboard">
        <Card title="Keyboard" actions={<span className="muted small">{mode === 'rest' ? 'hold keys to hold them on the C64 · SHIFT/C=/CTRL latch' : 'tap only'}</span>}>
          <Keyboard mode={status?.connected ? mode : 'none'} />
        </Card>
      </div>
      <GearSuggestions context="controller" using="keyboard" title="🕹 Play with a real joystick" />
    </div>
  )
}
