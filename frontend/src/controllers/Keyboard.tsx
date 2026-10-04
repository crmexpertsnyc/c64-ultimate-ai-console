import { useEffect, useRef, useState } from 'react'
import { input } from './inputQueue'

interface K { label: string; key: string; w?: number; shifted?: string; mod?: boolean; tapOnly?: boolean; kind?: string }

const k = (label: string, key: string, extra: Partial<K> = {}): K => ({ label, key, ...extra })

// C64 layout (REST key names from the Ultimate input API).
const ROWS: K[][] = [
  [k('←', 'arrow_left'), k('1', '1', { shifted: '!' }), k('2', '2', { shifted: '"' }), k('3', '3', { shifted: '#' }),
    k('4', '4', { shifted: '$' }), k('5', '5', { shifted: '%' }), k('6', '6', { shifted: '&' }), k('7', '7', { shifted: "'" }),
    k('8', '8', { shifted: '(' }), k('9', '9', { shifted: ')' }), k('0', '0'), k('+', 'plus'), k('−', 'minus'), k('£', 'pound'),
    k('CLR HOME', 'clr_home', { kind: 'fn' }), k('INST DEL', 'inst_del', { kind: 'fn' })],
  [k('CTRL', 'ctrl', { w: 1.5, mod: true, kind: 'mod' }), ...'QWERTYUIOP'.split('').map((c) => k(c, c.toLowerCase())),
    k('@', 'at'), k('*', 'star'), k('↑', 'arrow_up'), k('RESTORE', 'restore', { w: 1.5, tapOnly: true, kind: 'fn' })],
  [k('RUN STOP', 'run_stop', { w: 1.5, kind: 'fn' }), ...'ASDFGHJKL'.split('').map((c) => k(c, c.toLowerCase())),
    k(':', 'colon', { shifted: '[' }), k(';', 'semicolon', { shifted: ']' }), k('=', 'equals'),
    k('RETURN', 'return', { w: 2, kind: 'fn' })],
  [k('C=', 'commodore', { w: 1.25, mod: true, kind: 'mod' }), k('SHIFT', 'left_shift', { w: 1.75, mod: true, kind: 'mod' }),
    ...'ZXCVBNM'.split('').map((c) => k(c, c.toLowerCase())), k(',', 'comma', { shifted: '<' }),
    k('.', 'period', { shifted: '>' }), k('/', 'slash', { shifted: '?' }),
    k('SHIFT', 'right_shift', { w: 1.75, mod: true, kind: 'mod' }), k('CRSR ⇅', 'cursor_up_down', { kind: 'fn' }),
    k('CRSR ⇆', 'cursor_left_right', { kind: 'fn' })],
]
const FKEYS: K[] = [k('F1', 'f1'), k('F3', 'f3'), k('F5', 'f5'), k('F7', 'f7')]
const ARROWS: { label: string; key: string }[] = [
  { label: '▲', key: 'cursor_up' }, { label: '◀', key: 'cursor_left' }, { label: '▶', key: 'cursor_right' }, { label: '▼', key: 'cursor_down' },
]

/**
 * Full C64 keyboard. Modifiers (SHIFT, C=, CTRL) latch until the next key.
 * In REST mode keys are pressed on pointer-down and released on pointer-up, so
 * holding a key on screen holds it on the C64. Legacy mode can only tap.
 */
export function Keyboard({ mode }: { mode: 'rest' | 'legacy' | 'none' }) {
  const [mods, setMods] = useState<string[]>([])
  const [down, setDown] = useState<string | null>(null)
  const active = useRef<string | null>(null)
  const canHold = mode === 'rest'
  const disabled = mode === 'none'

  useEffect(() => () => { if (active.current) input.releaseAll() }, [])

  const combo = (key: string) => [...mods, key].join('+')

  const onDown = (key: K) => {
    if (disabled) return
    if (key.mod) {
      setMods((m) => (m.includes(key.key) ? m.filter((x) => x !== key.key) : [...m, key.key]))
      return
    }
    const c = combo(key.key)
    setDown(key.key)
    if (canHold && !key.tapOnly) {
      active.current = c
      input.key(c, 'press')
    }
  }

  const onUp = (key: K) => {
    if (key.mod || disabled) return
    setDown(null)
    if (canHold && !key.tapOnly) {
      if (active.current) input.key(active.current, 'release')
      active.current = null
    } else {
      input.key(mode === 'legacy' && mods.length === 0 ? key.key : combo(key.key), 'tap')
    }
    setMods([])
  }

  const render = (key: K, i: number) => (
    <button
      key={`${key.key}-${i}`}
      className={`key key-${key.kind ?? 'char'} ${mods.includes(key.key) ? 'latched' : ''} ${down === key.key ? 'held' : ''}`}
      style={{ flexGrow: key.w ?? 1 }}
      disabled={disabled}
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); onDown(key) }}
      onPointerUp={() => onUp(key)}
      onPointerCancel={() => onUp(key)}
      onContextMenu={(e) => e.preventDefault()}
      aria-label={key.label}
    >
      {key.shifted && <span className="key-shift">{key.shifted}</span>}
      <span>{key.label}</span>
    </button>
  )

  return (
    <div className={`keyboard ${disabled ? 'kbd-disabled' : ''}`}>
      <div className="kbd-main">
        {ROWS.map((row, r) => <div key={r} className="kbd-row">{row.map(render)}</div>)}
        <div className="kbd-row kbd-space">
          {render(k('SPACE', 'space', { w: 8 }), 0)}
        </div>
      </div>
      <div className="kbd-side">
        <div className="kbd-fkeys">{FKEYS.map(render)}</div>
        <div className="kbd-arrows">
          {ARROWS.map((a) => (
            <button key={a.key} className={`key key-fn arrow-${a.key}`} disabled={disabled}
              onClick={() => input.key(a.key, 'tap')} aria-label={a.key.replace('_', ' ')}>{a.label}</button>
          ))}
        </div>
        {mods.length > 0 && <div className="muted small">Latched: {mods.join(' + ').replace(/_/g, ' ')}</div>}
      </div>
    </div>
  )
}
