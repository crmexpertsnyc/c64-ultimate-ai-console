import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * 🧭 First-run tour: a short walk through the main places, opening each page behind a small card so you see it.
 * Shown once per device (after setup); restart it from Home or Settings ("Take the tour").
 */
export const TOUR_EVENT = 'c64-tour-start'
const DONE_KEY = 'c64.tour.done'

const STEPS: { to: string; icon: string; title: string; text: string }[] = [
  { to: '/', icon: '◉', title: 'Welcome to your C64 console',
    text: 'This is Home: what\'s new in the C64 world, games to continue, events near you, and a new C64 moment every day. The tour takes about a minute.' },
  { to: '/library', icon: '📚', title: 'Your library',
    text: 'All your games in one place, with box art and details. Pick one and press Play — it starts on your real C64 Ultimate.' },
  { to: '/stream', icon: '📺', title: 'The C64 screen',
    text: 'Watch and hear your real C64 here, from any device. Reset, power and the Ultimate menu are on this page too.' },
  { to: '/emulate', icon: '🕹', title: 'Browser Play',
    text: 'Away from the C64? Play the same games in the browser — your progress is saved so you can continue later.' },
  { to: '/controller', icon: '✥', title: 'Controller',
    text: 'Use your phone, keyboard or a gamepad as the joystick and keyboard for the real C64.' },
  { to: '/news', icon: '🆕', title: "What's new",
    text: 'New games and demos, news, videos and new hardware — checked automatically all day.' },
  { to: '/bbs', icon: '📟', title: 'Call a BBS',
    text: 'Bulletin boards are still alive! Open one in the browser, or let the console dial it on your real C64.' },
  { to: '/jukebox', icon: '🎵', title: 'Jukebox',
    text: 'Play SID music on the real SID chip — search any tune or composer, or ask for a themed station.' },
  { to: '/settings', icon: '⚙', title: 'Settings',
    text: 'Connection, AI assistant, remote access and a password for your console live here. Have fun — and you can take this tour again from Home anytime.' },
]

export function startTour(): void {
  window.dispatchEvent(new Event(TOUR_EVENT))
}

export function Tour() {
  const navigate = useNavigate()
  const [step, setStep] = useState<number | null>(null)

  const go = useCallback((i: number) => { setStep(i); navigate(STEPS[i].to) }, [navigate])
  const finish = useCallback(() => {
    setStep(null)
    try { localStorage.setItem(DONE_KEY, '1') } catch { /* private window */ }
  }, [])

  useEffect(() => {
    const start = () => go(0)
    window.addEventListener(TOUR_EVENT, start)
    let done = false
    try { done = localStorage.getItem(DONE_KEY) === '1' } catch { done = true }
    // first visit on this device: start once nothing else (e.g. the password prompt) has a dialog open
    const t = done ? 0 : window.setInterval(() => {
      if (document.querySelector('.modal')) return
      window.clearInterval(t)
      setStep((s) => s ?? 0)
    }, 1200)
    return () => { window.removeEventListener(TOUR_EVENT, start); window.clearInterval(t) }
  }, [go])

  useEffect(() => {
    if (step === null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') finish()
      else if (e.key === 'ArrowRight' && step < STEPS.length - 1) go(step + 1)
      else if (e.key === 'ArrowLeft' && step > 0) go(step - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, go, finish])

  if (step === null) return null
  const s = STEPS[step]
  const last = step === STEPS.length - 1
  return (
    <div className="tour-card" role="dialog" aria-label="Tour" aria-live="polite">
      <div className="tour-top">
        <span className="tour-icon" aria-hidden>{s.icon}</span>
        <strong>{s.title}</strong>
        <button className="btn btn-ghost btn-sm" onClick={finish} aria-label="Close the tour">✕</button>
      </div>
      <p>{s.text}</p>
      <div className="tour-foot">
        <span className="tour-dots" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
          {STEPS.map((_, i) => <i key={i} className={i === step ? 'on' : ''} />)}
        </span>
        {step > 0 && <button className="btn btn-sm" onClick={() => go(step - 1)}>Back</button>}
        {last
          ? <button className="btn btn-primary btn-sm" onClick={() => { finish(); navigate('/') }}>Start playing</button>
          : <button className="btn btn-primary btn-sm" onClick={() => go(step + 1)}>Next</button>}
      </div>
      {step === 0 && <button className="link small tour-skip" onClick={finish}>Skip the tour</button>}
    </div>
  )
}
