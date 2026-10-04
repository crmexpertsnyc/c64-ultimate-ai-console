import { useCallback, useEffect, useRef, useState } from 'react'
import { input } from './inputQueue'

type Dir = 'up' | 'down' | 'left' | 'right' | 'fire' | 'fire2' | 'fire3'

const KEYMAP: Record<string, Dir> = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  ' ': 'fire', Control: 'fire', z: 'fire', x: 'fire2', c: 'fire3',
}

/** On-screen joystick with real press/release (pointer down = press, up/leave = release). */
export function Joystick({ port, enabled, onPortChange, singleFire = false, ports = [1, 2] }: {
  port: number; enabled: boolean; onPortChange: (p: number) => void
  /** Joystick bridge: only the one real fire line exists. */
  singleFire?: boolean
  /** Ports that can be driven (a bridge may be wired to only one). */
  ports?: number[]
}) {
  const [held, setHeld] = useState<Set<Dir>>(new Set())
  const [capture, setCapture] = useState(false)
  const heldRef = useRef(held)
  heldRef.current = held

  const press = useCallback((d: Dir) => {
    if (!enabled || heldRef.current.has(d)) return
    setHeld((h) => new Set(h).add(d))
    input.joy([d], 'press', port)
  }, [enabled, port])

  const release = useCallback((d: Dir) => {
    if (!heldRef.current.has(d)) return
    setHeld((h) => { const n = new Set(h); n.delete(d); return n })
    input.joy([d], 'release', port)
  }, [port])

  // Physical keyboard → joystick when capture is on.
  useEffect(() => {
    if (!capture || !enabled) return
    const map = (k: string): Dir | undefined => {
      const d = KEYMAP[k]
      return singleFire && d?.startsWith('fire') ? 'fire' : d
    }
    const down = (e: KeyboardEvent) => {
      const d = map(e.key)
      if (!d || e.repeat) return
      e.preventDefault()
      press(d)
    }
    const up = (e: KeyboardEvent) => {
      const d = map(e.key)
      if (!d) return
      e.preventDefault()
      release(d)
    }
    const blur = () => heldRef.current.forEach((d) => release(d))
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
      blur()
    }
  }, [capture, enabled, press, release, singleFire])

  // Never leave anything held when the controller unmounts.
  useEffect(() => () => { if (heldRef.current.size) input.releaseAll() }, [])

  const btn = (d: Dir, label: string, cls = '') => (
    <button
      className={`joy-btn joy-${d} ${cls} ${held.has(d) ? 'held' : ''}`}
      disabled={!enabled}
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); press(d) }}
      onPointerUp={() => release(d)}
      onPointerCancel={() => release(d)}
      onLostPointerCapture={() => release(d)}
      onContextMenu={(e) => e.preventDefault()}
      aria-label={`${d} (hold)`}
      aria-pressed={held.has(d)}
    >{label}</button>
  )

  return (
    <div className="joystick">
      <div className="joy-top">
        <div className="seg" role="radiogroup" aria-label="Joystick port">
          {[1, 2].map((p) => (
            <button key={p} role="radio" aria-checked={port === p} className={`seg-btn ${port === p ? 'on' : ''}`}
              disabled={!ports.includes(p)} title={ports.includes(p) ? undefined : 'Not wired on the joystick bridge'}
              onClick={() => onPortChange(p)}>Port {p}</button>
          ))}
        </div>
        <label className="toggle">
          <input type="checkbox" checked={capture} disabled={!enabled} onChange={(e) => setCapture(e.target.checked)} />
          Arrow keys + Space
        </label>
      </div>
      <div className="joy-body">
        <div className="dpad">
          {btn('up', '▲')}
          {btn('left', '◀')}
          <div className="dpad-center" />
          {btn('right', '▶')}
          {btn('down', '▼')}
        </div>
        <div className="fire-cluster">
          {btn('fire', 'FIRE', 'fire-main')}
          {!singleFire && <div className="fire-row">
            {btn('fire2', 'FIRE 2', 'fire-alt')}
            {btn('fire3', 'FIRE 3', 'fire-alt')}
          </div>}
        </div>
      </div>
    </div>
  )
}
