import { useLive } from '../hooks/useLive'

/** File types the browser emulator can start (mirrors EMULATOR_FORMATS in the backend). */
export const BROWSER_FORMATS = new Set(['d64', 'd71', 'd81', 'g64', 'x64', 't64', 'tap', 'prg', 'p00', 'crt'])

export const canPlayInBrowser = (format?: string | null, category?: string | null) =>
  !!format && BROWSER_FORMATS.has(format.toLowerCase()) && category !== 'music'

export const C64_TIP = 'Loads on your real Commodore 64 Ultimate. Watch on your TV, or on the C64 Screen page with sound.'
export const BROWSER_TIP = 'Runs in an emulator on this device — your C64 is not used. Works anywhere, even away from home.'

/**
 * The two ways to play, always labelled the same way:
 *   📺 On my C64   — the real Commodore 64 Ultimate (needs it to be on and connected)
 *   💻 In browser  — an emulator on this phone/tablet/computer (works anywhere)
 * Whichever is available becomes the highlighted choice.
 */
export function PlayChoice({ onC64, onBrowser, browserOk = true, busy = false, size = 'sm' }: {
  onC64: () => void
  onBrowser: () => void
  browserOk?: boolean
  busy?: boolean
  size?: 'sm' | 'lg'
}) {
  const { status } = useLive()
  const c64Ok = !!status?.connected
  const cls = size === 'lg' ? 'btn-lg' : 'btn-sm'
  const c64Primary = c64Ok
  const browserPrimary = !c64Ok && browserOk
  return (
    <div className={`play-choice ${size}`} role="group" aria-label="How to play">
      <button className={`btn ${cls} ${c64Primary ? 'btn-primary' : ''}`} disabled={busy || !c64Ok} onClick={onC64}
        title={c64Ok ? C64_TIP : 'Your C64 Ultimate is not connected — use 💻 In browser, or turn the C64 on'}>
        📺 {size === 'lg' ? 'Play on my C64' : 'On my C64'}
      </button>
      <button className={`btn ${cls} ${browserPrimary ? 'btn-primary' : ''}`} disabled={busy || !browserOk} onClick={onBrowser}
        title={browserOk ? BROWSER_TIP : 'This file type cannot run in the browser emulator'}>
        💻 {size === 'lg' ? 'Play in browser' : 'In browser'}
      </button>
    </div>
  )
}

/** One-line explainer shown above game lists (dismissible). */
export function PlayModesHint() {
  const key = 'c64.hint.playModes'
  let hidden = false
  try { hidden = localStorage.getItem(key) === 'hidden' } catch { /* storage unavailable */ }
  if (hidden) return null
  const hide = (e: React.MouseEvent<HTMLButtonElement>) => {
    try { localStorage.setItem(key, 'hidden') } catch { /* ignore */ }
    e.currentTarget.closest('.play-modes-hint')?.remove()
  }
  return (
    <div className="play-modes-hint">
      <span><strong>📺 On my C64</strong> plays on your real Commodore 64 Ultimate (TV or the C64 Screen page).</span>
      <span><strong>💻 In browser</strong> plays in an emulator right on this device — anywhere, without the C64.</span>
      <button className="link" onClick={hide} aria-label="Hide this tip">Got it</button>
    </div>
  )
}
