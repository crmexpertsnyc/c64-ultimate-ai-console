import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { sendJoystick } from '../services/joystickSocket'

export type PadAction = 'up' | 'down' | 'left' | 'right' | 'a' | 'b' | 'x' | 'y' | 'start' | 'select'

/** Components can take over gamepad actions (e.g. the remote Ultimate menu): listen for
 * `c64-gamepad` and call `event.preventDefault()` to stop the default UI navigation. */
export const GAMEPAD_EVENT = 'c64-gamepad'

// Standard Gamepad mapping (https://w3c.github.io/gamepad/#remapping).
const BUTTONS: Record<number, PadAction> = {
  0: 'a', 1: 'b', 2: 'x', 3: 'y', 8: 'select', 9: 'start', 12: 'up', 13: 'down', 14: 'left', 15: 'right',
}
const REPEAT_DELAY = 380
const REPEAT_RATE = 120
const STICK = 0.55

function readPad(pad: Gamepad): Set<PadAction> {
  const held = new Set<PadAction>()
  for (const [i, action] of Object.entries(BUTTONS)) {
    if (pad.buttons[Number(i)]?.pressed) held.add(action)
  }
  const [x = 0, y = 0] = pad.axes
  if (x < -STICK) held.add('left')
  if (x > STICK) held.add('right')
  if (y < -STICK) held.add('up')
  if (y > STICK) held.add('down')
  return held
}

function focusables(): HTMLElement[] {
  const root = document.querySelector('.modal') ?? document.querySelector('.shell') ?? document.body
  return Array.from(root.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])',
  )).filter((el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'
  })
}

/** Move focus to the nearest element in a direction (simple spatial navigation). */
function moveFocus(dir: 'up' | 'down' | 'left' | 'right') {
  const items = focusables()
  if (!items.length) return
  const current = document.activeElement as HTMLElement | null
  if (!current || !items.includes(current)) {
    const first = items.find((el) => el.closest('main')) ?? items[0]
    first.focus()
    first.classList.add('gp-focus')
    return
  }
  const c = current.getBoundingClientRect()
  const cx = c.left + c.width / 2
  const cy = c.top + c.height / 2
  let best: HTMLElement | null = null
  let bestScore = Infinity
  for (const el of items) {
    if (el === current) continue
    const r = el.getBoundingClientRect()
    const x = r.left + r.width / 2
    const y = r.top + r.height / 2
    const dx = x - cx
    const dy = y - cy
    const along = dir === 'left' ? -dx : dir === 'right' ? dx : dir === 'up' ? -dy : dy
    const across = dir === 'left' || dir === 'right' ? Math.abs(dy) : Math.abs(dx)
    if (along <= 4) continue
    const score = along + across * 2.2
    if (score < bestScore) {
      bestScore = score
      best = el
    }
  }
  if (best) {
    document.querySelectorAll('.gp-focus').forEach((el) => el.classList.remove('gp-focus'))
    best.focus()
    best.classList.add('gp-focus')
    best.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })
  }
}

const JOY: Partial<Record<PadAction, string>> = { up: 'up', down: 'down', left: 'left', right: 'right', a: 'fire', b: 'fire2', x: 'fire3' }

/**
 * Gamepad support (couch mode). By default it navigates the app. Components can claim actions
 * (remote Ultimate menu). On the Display page with firmware that supports network input, the pad
 * becomes the C64 joystick (press/release follow the real buttons).
 */
export function useGamepad({ joystickMode, joystickPort, singleFire = false, onConnect }: {
  joystickMode: boolean
  joystickPort: number
  /** The joystick bridge has one fire line: B and X also fire instead of FIRE 2/3. */
  singleFire?: boolean
  onConnect?: (name: string) => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const state = useRef({ joystickMode, joystickPort, singleFire, path: location.pathname })
  state.current = { joystickMode, joystickPort, singleFire, path: location.pathname }

  useEffect(() => {
    let raf = 0
    let prev = new Set<PadAction>()
    const nextRepeat: Partial<Record<PadAction, number>> = {}
    const joyHeld = new Set<string>()

    const releaseJoystick = () => {
      if (joyHeld.size) {
        sendJoystick(state.current.joystickPort, [])
        joyHeld.clear()
      }
    }

    const fire = (action: PadAction) => {
      const ev = new CustomEvent(GAMEPAD_EVENT, { detail: { action }, cancelable: true })
      if (!window.dispatchEvent(ev)) return // claimed by a component
      const el = document.activeElement as HTMLElement | null
      switch (action) {
        case 'up': case 'down': case 'left': case 'right':
          moveFocus(action)
          break
        case 'a':
          el?.click()
          break
        case 'b': {
          const close = document.querySelector<HTMLElement>('.modal [aria-label="Close"]')
          if (close) close.click()
          else if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
          else navigate(-1)
          break
        }
        case 'start':
          navigate('/stream')
          break
        case 'select':
          navigate('/')
          break
      }
    }

    const loop = () => {
      const pad = Array.from(navigator.getGamepads?.() ?? []).find((p) => p && p.connected) ?? null
      const held = pad ? readPad(pad) : new Set<PadAction>()
      const now = performance.now()
      const { joystickMode: joy, path } = state.current
      if (path.startsWith('/emulate/')) {
        // The emulator reads the gamepad itself; app navigation would fight it.
        releaseJoystick()
        prev = held
        raf = requestAnimationFrame(loop)
        return
      }

      if (joy && path === '/stream') {
        // Joystick passthrough: mirror the pad onto the C64 joystick.
        const want = new Set([...held].map((a) => JOY[a]).filter(Boolean)
          .map((j) => (state.current.singleFire && j!.startsWith('fire') ? 'fire' : j)) as string[])
        const changed = want.size !== joyHeld.size || [...want].some((j) => !joyHeld.has(j))
        if (changed) {
          sendJoystick(state.current.joystickPort, [...want])
          joyHeld.clear()
          want.forEach((j) => joyHeld.add(j))
        }
        if (held.has('start') && !prev.has('start')) navigate('/')
      } else {
        releaseJoystick()
        for (const a of held) {
          if (!prev.has(a)) {
            fire(a)
            nextRepeat[a] = now + REPEAT_DELAY
          } else if (['up', 'down', 'left', 'right'].includes(a) && now >= (nextRepeat[a] ?? Infinity)) {
            fire(a)
            nextRepeat[a] = now + REPEAT_RATE
          }
        }
      }
      prev = held
      raf = requestAnimationFrame(loop)
    }

    const announced = new Set<string>()
    const onConnectEv = (e: GamepadEvent) => {
      // Some controllers register as two devices; announce each name once.
      const name = e.gamepad.id.split('(')[0].trim() || 'Controller'
      if (announced.has(name)) return
      announced.add(name)
      onConnect?.(name)
    }
    window.addEventListener('gamepadconnected', onConnectEv)
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('gamepadconnected', onConnectEv)
      releaseJoystick()
    }
  }, [navigate, onConnect])
}
