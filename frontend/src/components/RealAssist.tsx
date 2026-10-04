import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../services/api'
import { analyze, keyLabel } from '../services/startAnalyzer'
import type { Analysis, InputProfileInfo, ScreenSample } from '../services/startAnalyzer'
import type { InputSignals } from '../services/autoInput'
import { Copilot } from './Copilot'
import { HintPanel } from './HintPanel'
import { useToast } from './Toasts'

interface Progress {
  gameId: number | null
  metrics: Record<string, number>
  newBest: number | null
  unlocked: { title: string; icon: string; description: string }[]
  sample: ScreenSample
}

const STATE_LABEL: Record<string, string> = {
  loading: 'loading', basic: 'BASIC prompt', intro: 'intro', title: 'title screen', trainer: 'trainer / options',
  menu: 'menu or question', gameplay: 'playing', unknown: 'running',
}

/** How the console can press a C64 key on the real machine (this firmware: keyboard buffer + joystick bridge). */
async function pressOnC64(key: string): Promise<void> {
  if (['FIRE', 'UP', 'DOWN', 'LEFT', 'RIGHT'].includes(key)) { await api.joystick([key.toLowerCase()], 'tap'); return }
  if (key === 'FIRE+SPACE') { await api.type(' '); return }
  if (key === 'RUN') { await api.type('RUN', true); return }
  if (key === 'RETURN') { await api.key('return'); return }
  if (key === 'SPACE' || key === 'ANY') { await api.type(' '); return }
  if (key === 'RUN/STOP') { await api.key('run_stop'); return }
  if (/^F[1357]$/.test(key)) { await api.key(key.toLowerCase()); return }
  if (/^[A-Z0-9]$/.test(key)) { await api.type(key); return }
  throw new Error(`press ${key} on the C64`)
}

/**
 * 🤖 AI assist for the REAL C64: reads what the C64 Ultimate shows (from its memory, read-only), says what the
 * game is waiting for — and presses it when the console can —, gives 💡 hints and runs the 🧭 co-pilot for text
 * adventures. Scores read from the screen count for 🏆 achievements and the family leaderboard (verified).
 */
export function RealAssist({ connected }: { connected: boolean }) {
  const toast = useToast()
  const [p, setP] = useState<Progress | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [panel, setPanel] = useState<'hint' | 'copilot' | null>(null)
  const [busy, setBusy] = useState(false)
  const sample = useRef<ScreenSample | null>(null)
  const profile = useRef<{ gameId: number | null; info: InputProfileInfo | null }>({ gameId: null, info: null })
  const basicSince = useRef(0)
  const best = useRef<number | null>(null)

  useEffect(() => {
    if (!connected) return
    let stop = false
    const tick = async () => {
      try {
        const r = await api.deviceProgress()
        if (stop) return
        sample.current = r.sample
        setError(null)
        setP(r)
        if (r.gameId !== profile.current.gameId) {
          profile.current = { gameId: r.gameId, info: null }
          best.current = null
          if (r.gameId) api.inputProfile(r.gameId).then((info) => { profile.current.info = info }).catch(() => {})
        }
        const s = r.sample
        if (s.basicBanner && s.lastLine === 'READY.') { if (!basicSince.current) basicSince.current = Date.now() } else basicSince.current = 0
        setAnalysis(analyze(s, { fps: 50 } as InputSignals, profile.current.info, {
          gameplay: !!r.metrics.score || !!r.metrics.level, nextStep: 0,
          basicSince: basicSince.current ? Date.now() - basicSince.current : 0, elapsed: 60,
        }))
        for (const u of r.unlocked) toast(`${u.icon} Achievement unlocked: ${u.title} — ${u.description}`, 'ok')
        if (r.newBest && (best.current === null || r.newBest > best.current)) best.current = r.newBest
      } catch (e) {
        if (!stop) setError(errorMessage(e))
      }
    }
    tick()
    const t = window.setInterval(tick, 2000)
    return () => { stop = true; window.clearInterval(t) }
  }, [connected, toast])

  if (!connected) return null
  if (error && !p) return <div className="real-assist muted small">🤖 AI assist: {error}</div>

  const act = analysis?.action
  const doIt = async () => {
    if (!act) return
    setBusy(true)
    try {
      await pressOnC64(act.key)
      toast(`⌨ Pressed ${keyLabel(act.key)} on your C64`, 'ok')
    } catch {
      toast(`Press ${keyLabel(act.key)} on the C64's keyboard (this firmware can't press it over the network)`, 'error')
    } finally {
      setBusy(false)
    }
  }
  const m = p?.metrics ?? {}
  const gameId = p?.gameId ?? null
  return (
    <div className="real-assist">
      <div className="real-assist-bar">
        <strong>🤖 AI assist</strong>
        <span className="muted small">reading your C64's screen{analysis ? ` · ${STATE_LABEL[analysis.state] ?? analysis.state}` : ''}{p?.sample.mode && p.sample.mode !== 'text' ? ` (${p.sample.mode})` : ''}</span>
        {act && analysis?.state !== 'gameplay' && (
          <button className="btn btn-sm emu-next emu-start" onClick={doIt} disabled={busy} title={act.reason}>
            ▶ Next: {keyLabel(act.key)} — press it</button>
        )}
        {analysis?.port && analysis.port.confidence >= 75 && <span className="small">🕹 port {analysis.port.port}</span>}
        {(m.score !== undefined || m.level !== undefined) && (
          <span className="score-chip">🏆 {m.score !== undefined ? m.score.toLocaleString() : ''}{m.level !== undefined ? ` · round ${m.level}` : ''}
            {m.lives !== undefined ? ` · ${m.lives} lives` : ''}{best.current ? ` · best ${best.current.toLocaleString()}` : ''} <span className="muted">✓ verified</span></span>
        )}
        <span className="row-actions">
          <button className={`btn btn-sm ${panel === 'hint' ? 'btn-primary' : 'btn-ghost'}`} disabled={!gameId}
            onClick={() => setPanel(panel === 'hint' ? null : 'hint')} title={gameId ? 'Spoiler-free help for what is on screen' : 'Start a game from the library first'}>💡 Stuck?</button>
          <button className={`btn btn-sm ${panel === 'copilot' ? 'btn-primary' : 'btn-ghost'}`} disabled={!gameId}
            onClick={() => setPanel(panel === 'copilot' ? null : 'copilot')} title="Text adventure co-pilot — types commands on the C64">🧭 Co-pilot</button>
        </span>
      </div>
      {act && analysis?.state !== 'gameplay' && <p className="muted small" style={{ margin: '.2rem 0 0' }}>{act.reason}</p>}
      {panel === 'hint' && gameId && <HintPanel gameId={gameId} screen={() => sample.current} onClose={() => setPanel(null)} />}
      {panel === 'copilot' && gameId && (
        <Copilot gameId={gameId} screen={() => sample.current}
          type={async (t) => { await api.type(t.replace(/\n$/, ''), t.endsWith('\n')); return true }}
          onClose={() => setPanel(null)} />
      )}
    </div>
  )
}
