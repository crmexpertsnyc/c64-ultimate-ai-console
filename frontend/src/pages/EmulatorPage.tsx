import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { SaveInfo, SmartStep } from '../services/api'
import type { Guide } from '../components/GuideCard'
import { useToast } from '../components/Toasts'
import { Thumbs } from '../components/Thumbs'
import { currentProfile } from '../services/profile'
import { Copilot } from '../components/Copilot'
import { HangRescue } from '../components/HangRescue'
import { HintPanel } from '../components/HintPanel'
import { ReportProblem } from '../components/ReportProblem'
import { NetplayHost } from '../components/NetplayHost'
import { TransitionTracker, decideMode, gameplayConfidence, loadProfile, saveProfile } from '../services/autoInput'
import type { GameProfile, InputMode, InputPref, InputSignals, Signal } from '../services/autoInput'
import { StartLearner, analyze, keyLabel, pcKey } from '../services/startAnalyzer'
import type { Analysis, InputProfileInfo, Keymap, ScreenSample } from '../services/startAnalyzer'

interface EmuFile { mediaId: number; name: string; format: string; disk: number; url: string }
interface EmuInfo {
  id: number; title: string; files: EmuFile[]; bundleUrl?: string | null; joystickPort?: number | null
  smartStart?: { steps: SmartStep[]; auto: boolean; source: 'recorded' | 'guide' } | null
  guide?: Guide | null
  inputProfile?: InputProfileInfo | null
  genre?: string | null
  styleTags?: string[]
  hangs?: number
  issues?: { id: number; category: string; occurrences: number }[]
}

// A guide's start key (e.g. "RETURN", "F7", "FIRE") as a smart-start step.
function guideStep(k: string): SmartStep {
  if (k === 'FIRE') return { key: 'FIRE' }
  if (k === 'RUN/STOP' || k === 'RUN' || k === 'ANY' || k === 'FIRE+SPACE' || k === 'SPACE' || k === 'RETURN') return { key: k }
  if (k === 'RESTORE') return { key: 'RESTORE' }
  if (/^F[1357]$/.test(k)) return { key: k, code: k, keyCode: 111 + Number(k.slice(1)) }
  if (['UP', 'DOWN', 'LEFT', 'RIGHT'].includes(k)) return { key: 'JOY', code: 'Arrow' + k[0] + k.slice(1).toLowerCase() }
  if (/^[0-9]$/.test(k)) return { key: k, code: 'Digit' + k, keyCode: 48 + Number(k) }
  return { key: k.toLowerCase(), code: 'Key' + k, keyCode: k.charCodeAt(0) }
}

// [C64 key, PC key in play mode, PC key in type mode]
const KEYS: [string, string, string][] = [
  ['Joystick', 'Arrow keys · gamepad · touch', 'Gamepad / touch'],
  ['Fire', 'Space · gamepad A/B', 'Gamepad / touch'],
  ['Letters, numbers, Y/N', 'Type normally', 'Type normally'],
  ['RUN/STOP', 'Ctrl+R or button', 'Ctrl+R or button'],
  ['C= (Commodore)', 'Ctrl or button', 'Ctrl or button'],
  ['RESTORE', 'Page Up or button', 'Page Up or button'],
  ['RETURN', 'Enter', 'Enter'],
  ['Space bar', 'gamepad X · on-screen keyboard', 'Space'],
  ['Cursor keys', 'on-screen keyboard', 'Arrow keys'],
  ['F1 / F3 / F5 / F7', 'F1–F8 buttons · F1 / F3 / F5 / F7', 'F1–F8 buttons · F1 / F3 / F5 / F7'],
  ['F2 / F4 / F6 / F8', 'F1–F8 buttons · F2 / F4 / F6 / F8', 'F1–F8 buttons · F2 / F4 / F6 / F8'],
  ['CTRL', 'Tab', 'Tab'],
  ['INST/DEL', 'Delete', 'Delete'],
  ['CLR/HOME', 'Home', 'Home'],
  ['£  ↑  ←', 'End  \\  `', 'End  \\  `'],
  ['@  *  =', '[  ]  Page Down', '[  ]  Page Down'],
  ['Swap joystick port', 'Ctrl+Alt+P', 'Ctrl+Alt+P'],
  ['Type ⇄ Play', 'Ctrl+Alt+G', 'Ctrl+Alt+G'],
  ['Leave the game', 'Shift+Esc or ← Back', 'Shift+Esc or ← Back'],
]

/**
 * Plays a library title in the browser (EmulatorJS running VICE) — on this device, not the real C64.
 * Works anywhere the console is reachable, with keyboard, touch controls and gamepads.
 */
export function EmulatorPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { status } = useLive()
  const [info, setInfo] = useState<EmuInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [file, setFile] = useState(0)
  const fileRef = useRef(0)
  fileRef.current = file
  const [nonce, setNonce] = useState(0)
  // Input: AUTO decides between TYPE and PLAY (services/autoInput.ts); Type/Play buttons override it.
  const [pref, setPref] = useState<InputPref>('auto')
  const [mode, setMode] = useState<InputMode>('type')
  const typing = mode === 'type'
  const [portPref, setPortPref] = useState<'auto' | 1 | 2>('auto')
  const [profile, setProfile] = useState<GameProfile | null>(null)
  const [signals, setSignals] = useState<InputSignals | null>(null)
  const [confidence, setConfidence] = useState<{ score: number; signals: Signal[]; likely: boolean } | null>(null)
  const [debug, setDebug] = useState(() => {
    try { return new URLSearchParams(window.location.search).has('debug') || localStorage.getItem('c64.inputDebug') === '1' } catch { return false }
  })
  const tracker = useRef(new TransitionTracker())
  const hadPad = useRef(false)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const analysisRef = useRef<Analysis | null>(null)
  analysisRef.current = analysis
  const detectedPort = analysis?.port && analysis.port.confidence >= 85 ? analysis.port.port : null
  const autoPort = profile?.joystickPort ?? detectedPort ?? (info?.inputProfile?.joystickPort === 1 || info?.joystickPort === 1 ? 1 : 2)
  const port = portPref === 'auto' ? autoPort : portPref
  const [showKeys, setShowKeys] = useState(false)
  const [save, setSave] = useState<SaveInfo | null>(null)
  const [resumeAsked, setResumeAsked] = useState(false)
  const [saving, setSaving] = useState(false)
  // Smart start: a recorded key sequence replayed on boot, or the guide's start keys step by step.
  const [recording, setRecording] = useState(false)
  const [guideStepIx, setGuideStepIx] = useState(0)
  const resumedRef = useRef(false)
  const autoRanRef = useRef(false)
  const leftRef = useRef(false)
  // Startup analyzer (services/startAnalyzer.ts): what the game waits for, learned per game.
  const [screen, setScreen] = useState<ScreenSample | null>(null)
  const [autoStart, setAutoStart] = useState(() => { try { return localStorage.getItem('c64.autoStart') === '1' } catch { return false } })
  const [escRunstop, setEscRunstop] = useState(() => { try { return localStorage.getItem('c64.escRunstop') === '1' } catch { return false } })
  const learner = useRef(new StartLearner())
  const stepsDone = useRef(0)
  const sentFor = useRef(new Set<string>())
  const sigSince = useRef({ sig: '', t: 0 })
  const basicSince = useRef(0)
  const savedLearned = useRef('')
  const escHinted = useRef(false)
  // Recommendations learn from browser play: a start, then the minutes actually played (tab visible).
  const playStart = useRef(0)
  const sentPlay = useRef(false)
  const gameIdRef = useRef<number | null>(null) // (read after unmount, when state is gone)
  const flushSession = () => {
    const gameId = gameIdRef.current
    if (!gameId || !playStart.current) return
    const minutes = (Date.now() - playStart.current) / 60000
    playStart.current = 0
    if (minutes < 0.5) return
    const body = JSON.stringify({ kind: 'session', gameId, minutes: Math.min(minutes, 24 * 60) })
    try {
      if (!navigator.sendBeacon(`/api/taste/event?profile=${currentProfile()}`, new Blob([body], { type: 'application/json' }))) throw new Error('beacon')
    } catch { api.tasteEvent('session', gameId, minutes).catch(() => {}) }
  }
  useEffect(() => { gameIdRef.current = info?.id ?? null }, [info?.id])
  // Leaving by any route (sidebar, closing the tab): count the minutes played.
  useEffect(() => {
    window.addEventListener('pagehide', flushSession)
    return () => { window.removeEventListener('pagehide', flushSession); flushSession() }
  }, [])
  const [recentKeys, setRecentKeys] = useState<{ k: string; t: number }[]>([])
  // AI helpers: 💡 hints, 🧭 text adventure co-pilot, 🛟 rescue when a game hangs while loading.
  const [panel, setPanel] = useState<'hint' | 'copilot' | 'netplay' | null>(null)
  // 🐞 Compatibility log: automatic reports (won't load / never starts / hangs) and ⚑ Report a problem.
  const [reporting, setReporting] = useState(false)
  const autoReported = useRef(new Set<string>())
  const workedSent = useRef(false)
  const [rescue, setRescue] = useState<'before' | 'now' | null>(null)
  const staticSince = useRef(0)
  const rescueShown = useRef(false)
  const toast = useToast()
  const frame = useRef<HTMLIFrameElement>(null)

  // The player page exposes window.c64 (same origin) for these switches.
  const player = () => (frame.current?.contentWindow as (Window & { c64?: {
    typing(on: boolean): void; port(n: number): void; focus(): void; key(name: string, down: boolean): void
    forwardKey(init: Record<string, unknown>): void; fps(): number; inputState(): InputSignals
    disk(n: number): number; currentDisk(): number
    save(kind: 'auto' | 'manual'): Promise<number>; resume(): Promise<boolean>; started(): boolean
    recordStart(): boolean; stopRecording(): SmartStep[]; playStep(s: SmartStep): Promise<boolean>
    runStart(steps: SmartStep[], isCancelled: () => boolean): Promise<boolean>
    keymap(m: Partial<Keymap>): void; escRunstop(on: boolean): void; screen(): ScreenSample | null
    recentInputs(): { k: string; t: number }[]; keys(): Keymap & { escRunstop: boolean }; allowUnload(): void
    type(text: string): Promise<boolean>
    errors(): string[]; screenshot(): Promise<string | null>
  } }) | null)?.c64
  const [fps, setFps] = useState(0)
  const [fast, setFast] = useState(false)

  // Give the game keyboard focus (focus sits on this page after clicking a button in the bar).
  const focusGame = () => { frame.current?.focus(); player()?.focus() }

  // Keys pressed while this page (not the game) has focus — e.g. right after clicking a button in the
  // bar — go to the game exactly as if typed there (letters, Enter, joystick keys, Ctrl+R = RUN/STOP …).
  useEffect(() => {
    const fwd = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && ['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName)) return
      if (e.key === 'Escape' && e.shiftKey) return // leave: handled below
      player()?.forwardKey({ type: e.type, key: e.key, code: e.code, keyCode: e.keyCode, location: e.location,
        shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey, metaKey: e.metaKey, repeat: e.repeat })
      if (e.key.startsWith('Arrow') || e.key === ' ' || e.key === 'Tab' || e.key === 'Backspace' || /^F\d+$/.test(e.key)
        || (e.ctrlKey && ['KeyR', 'KeyG', 'KeyP'].includes(e.code)) || ['PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) e.preventDefault()
      if (e.type === 'keyup') focusGame() // next keys go straight to the game
    }
    window.addEventListener('keydown', fwd)
    window.addEventListener('keyup', fwd)
    return () => { window.removeEventListener('keydown', fwd); window.removeEventListener('keyup', fwd) }
  })

  // Speed meter.
  useEffect(() => {
    const t = window.setInterval(() => {
      setFps(player()?.fps() ?? 0)
      // Keep the disk picker in step with swaps made from the emulator's own Disks menu.
      const d = player()?.currentDisk() ?? -1
      if (d >= 0) setFile(d)
    }, 1000)
    return () => window.clearInterval(t)
  })
  // Leaving saves automatically, so the game can be continued later on any device.
  const leave = async () => {
    flushSession()
    try { player()?.allowUnload() } catch { /* player gone */ }
    if (player()?.started()) {
      try { await Promise.race([player()!.save('auto'), new Promise((r) => setTimeout(r, 4000))]) } catch { /* leave anyway */ }
    }
    navigate(tvMode ? '/tv' : '/emulate')
  }
  // 📺 From TV mode: full screen, and Select + Start on a gamepad goes back to the TV home.
  const tvMode = new URLSearchParams(window.location.search).has('tv')
  useEffect(() => {
    if (!tvMode) return
    let raf = 0, since = 0
    const tick = (t: number) => {
      const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) as Gamepad[] : []
      const both = pads.some((p) => p.buttons[8]?.pressed && p.buttons[9]?.pressed)
      if (both) { if (!since) since = t; else if (t - since > 600) { since = -1e9; leave() } } else if (since > 0) since = 0
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [tvMode]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveNow = async () => {
    setSaving(true)
    try {
      await player()?.save('manual')
      setSave(await api.saveInfo(Number(id)))
      toast('💾 Saved — continue on any device with ⟲ Resume', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setSaving(false)
      focusGame()
    }
  }

  const resume = async () => {
    resumedRef.current = true
    setResumeAsked(true)
    try {
      await player()?.resume()
      toast('⟲ Resumed where you left off', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
    focusGame()
  }

  const startFresh = async () => {
    setResumeAsked(true)
    focusGame()
  }

  // ---- 🐞 diagnostics for problem reports
  const collectDiagnostics = async (withShot = true) => {
    let sc: ScreenSample | null = null, errs: string[] = [], shot: string | null = null, keys: Keymap | null = null
    let sig: InputSignals | null = null, inputs: { k: string; t: number }[] = [], audio: string[] = []
    try {
      const p = player()
      sc = p?.screen() ?? null
      errs = p?.errors() ?? []
      keys = p?.keys() ?? null
      sig = p?.inputState() ?? null
      inputs = p?.recentInputs() ?? []
      audio = ((frame.current?.contentWindow as unknown as { __audioState?: () => string[] })?.__audioState?.() ?? [])
      if (withShot) shot = (await p?.screenshot()) ?? null
    } catch { /* player not running */ }
    const file = info?.files[info?.bundleUrl ? (player()?.currentDisk() ?? 0) : fileRef.current] ?? info?.files[0]
    const diagnostics: Record<string, unknown> = {
      fileName: file?.name, disk: file?.disk, format: file?.format, bundle: !!info?.bundleUrl,
      started: !!sig?.started, secondsSinceStart: sig?.startedAt ? Math.round((Date.now() - sig.startedAt) / 1000) : null,
      fps: sig?.fps ?? null, screenMode: sc?.mode ?? null, state: analysisRef.current?.state ?? null,
      screenText: sc?.mode === 'text' ? [sc.staticText, sc.scrollText.slice(-200)].filter(Boolean).join(' / ').slice(0, 1500) : '',
      recentInputs: inputs.slice(-12).map((x) => x.k), errors: errs, audio: audio.join(','),
      port, keymap: keys ? `${keys.arrows}/${keys.space}` : null, mode: `${pref}/${mode}`,
      resumed: resumedRef.current, fastMode: fast, userAgent: navigator.userAgent,
      pads: sig?.pads.map((x) => x.id.split('(')[0].trim()) ?? [],
    }
    return { diagnostics, screenshot: shot }
  }
  const autoReport = async (category: string, extra: Record<string, unknown> = {}) => {
    const gid = info?.id ?? Number(id)
    if (!gid || autoReported.current.has(category)) return
    autoReported.current.add(category)
    const { diagnostics, screenshot } = await collectDiagnostics(category === 'hangs')
    api.reportIssue({ gameId: gid, category, source: 'auto', diagnostics: { ...diagnostics, ...extra }, screenshot }).catch(() => {})
  }

  // ---- smart start
  const guideKeys = info?.guide?.startKeys ?? []
  const startRecording = () => {
    if (player()?.recordStart()) { setRecording(true); toast('⏺ Recording — get past the intro, then press ⏹ Stop recording', 'ok') }
    focusGame()
  }
  const stopRecording = async () => {
    const steps = player()?.stopRecording() ?? []
    setRecording(false)
    focusGame()
    if (!info || !steps.length) { toast('Nothing recorded', 'error'); return }
    try {
      const smartStart = { steps, auto: true, source: 'recorded' as const }
      await api.putSmartStart(info.id, smartStart)
      setInfo({ ...info, smartStart })
      toast(`⏩ Smart start saved (${steps.length} steps) — it runs automatically next time`, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }
  const replayStart = (e?: React.MouseEvent) => {
    if (e?.shiftKey) { forgetStart(); return }
    if (!info?.smartStart) return
    // Replay "now": shift the recorded times so the first step happens right away.
    const first = info.smartStart.steps[0]?.at ?? 0
    const steps = info.smartStart.steps.map((st) => ({ ...st, at: Math.max(0, (st.at ?? 0) - first) }))
    ;(async () => { for (const st of steps) { await player()?.playStep(st); await new Promise((r) => setTimeout(r, 400)) } })()
    focusGame()
  }
  const forgetStart = async () => {
    if (!info) return
    await api.deleteSmartStart(info.id).catch(() => {})
    setInfo({ ...info, smartStart: null })
    toast('Smart start deleted', 'ok')
  }
  const nextGuideKey = async () => {
    const k = guideKeys[guideStepIx]
    if (!k) return
    await player()?.playStep(guideStep(k))
    setGuideStepIx((i) => i + 1)
    focusGame()
  }
  // Recorded smart start: run it when the game boots — unless you chose to resume a save.
  useEffect(() => {
    const ss = info?.smartStart
    if (!ss || ss.source !== 'recorded' || !ss.auto || autoRanRef.current || save === null) return
    if (save.exists && !resumeAsked) return // waiting for Resume / Start fresh
    if (resumedRef.current) return
    const p = player()
    if (!p?.runStart) return // player page not ready yet: this effect runs again on the next render
    autoRanRef.current = true
    toast('⏩ Smart start: skipping the intro for you…', 'ok')
    p.runStart(ss.steps, () => resumedRef.current || leftRef.current)
  })
  useEffect(() => () => { leftRef.current = true }, [])
  useEffect(() => {
    if (!info) return
    const t = window.setTimeout(() => {
      let started = false
      try { started = !!player()?.started() } catch { /* not there */ }
      if (!started && !leftRef.current) autoReport('no_start')
    }, 75000)
    return () => window.clearTimeout(t)
  }, [info?.id, nonce]) // eslint-disable-line react-hooks/exhaustive-deps

  // Tab hidden (phone locked, app switched): save quietly so nothing is lost.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') {
        flushSession()
        if (player()?.started()) player()!.save('auto').catch(() => {})
      } else if (sentPlay.current && !playStart.current) {
        playStart.current = Date.now() // back to the game: keep counting
      }
    }
    document.addEventListener('visibilitychange', onHide)
    return () => document.removeEventListener('visibilitychange', onHide)
  })

  // Shift+Esc inside the game (the player posts a message) or on this page leaves the game.
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin === window.location.origin && e.data?.type === 'c64-exit') leave()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && e.shiftKey) leave() }
    window.addEventListener('message', onMsg)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('keydown', onKey) }
  })

  // On-screen C64 keys: held while the button is held, like the real key.
  const keyBtn = (name: string, label: string, tip: string) => (
    <button className="btn btn-sm emu-key" title={tip}
      onPointerDown={(e) => { e.preventDefault(); player()?.key(name, true) }}
      onPointerUp={() => { player()?.key(name, false); focusGame() }}
      onPointerLeave={() => player()?.key(name, false)}
      onContextMenu={(e) => e.preventDefault()}>{label}</button>
  )
  // Manual choice overrides Auto until the next game (or until Auto is chosen again).
  const choose = (p: InputPref) => {
    setPref(p)
    if (p === 'play' && info && signals?.startedAt && !profile?.gameplayAfter) {
      setProfile(saveProfile(info.id, { gameplayAfter: Math.round((Date.now() - signals.startedAt) / 1000) }))
    }
    focusGame()
  }
  const choosePort = (v: string) => {
    const p = v === 'auto' ? 'auto' : (Number(v) as 1 | 2)
    setPortPref(p)
    if (p !== 'auto' && info) setProfile(saveProfile(info.id, { joystickPort: p })) // remember for this game
    focusGame()
  }

  // Apply mode and port to the player whenever they change (and after the player reloads).
  // (Never let a player hiccup take the page down: these run while the emulator may still be starting.)
  const autoKeymap: Keymap | null = pref === 'auto' && mode === 'type' && analysis ? analysis.keymap : null
  useEffect(() => {
    try {
      if (autoKeymap) player()?.keymap(autoKeymap)
      else player()?.typing(mode === 'type')
    } catch { /* retried when started */ }
  }, [mode, nonce, info, signals?.started, autoKeymap?.arrows, autoKeymap?.space]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { try { player()?.escRunstop(escRunstop) } catch { /* retried */ } }, [escRunstop, nonce, info, signals?.started])
  useEffect(() => { try { player()?.port(port) } catch { /* retried when started */ } }, [port, nonce, info, signals?.started])

  // 🏆 Score, round and lives read from the game's own screen → achievements and the family high-score table.
  const [score, setScore] = useState<{ metrics: Record<string, number>; best: number | null }>({ metrics: {}, best: null })
  useEffect(() => {
    if (!info) return
    let busy = false
    let asked = false
    setScore({ metrics: {}, best: null })
    const t = window.setInterval(() => {
      let sc: ScreenSample | null = null
      try { sc = player()?.screen() ?? null } catch { return }
      if (!sc || sc.mode !== 'text' || busy || document.hidden) return
      busy = true
      const playing = analysisRef.current?.state === 'gameplay'
      api.gameProgress(info.id, { rows: sc.rows, rawRows: sc.rawRows, mode: sc.mode }, playing)
        .then((r) => {
          setScore((o) => ({ metrics: r.metrics, best: r.newBest && (!o.best || r.newBest > o.best) ? r.newBest : o.best }))
          for (const u of r.unlocked) toast(`${u.icon} Achievement unlocked: ${u.title} — ${u.description}`, 'ok')
          // the first time a score / round shows up in a game without its own achievements, the AI drafts some
          if (!asked && playing && (r.metrics.score !== undefined || r.metrics.level !== undefined)) {
            asked = true
            api.gameAchievements(info.id).then((g) => {
              if (!g.hasGameAchievements) {
                api.makeAchievements(info.id).then((m) => { if (m.achievements.length) toast(`🏆 ${m.achievements.length} achievements to chase in ${info.title} — see the game page`, 'ok') }).catch(() => {})
              }
            }).catch(() => {})
          }
        })
        .catch(() => {})
        .finally(() => { busy = false })
    }, 2000)
    return () => window.clearInterval(t)
  }, [info?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto mode: read the player's signals twice a second and decide.
  useEffect(() => {
    const t = window.setInterval(() => {
      let sig: InputSignals | undefined
      try { sig = player()?.inputState() } catch { return }
      if (!sig) return
      tracker.current.update(sig)
      // 🛟 Stuck while loading? The screen froze (no change for 45 s) soon after the game started or after the
      // player's last key press, nothing was pressed since, and nothing on screen asks for a key.
      {
        const now2 = Date.now()
        const recent = sig.motion.slice(-4)
        const still = recent.length >= 4 && Math.max(...recent) < 0.003
        staticSince.current = still ? staticSince.current || now2 : 0
        const lastInput = Math.max(sig.lastKeyAt, sig.lastPadAt, sig.startedAt)
        const frozeSoonAfter = staticSince.current && staticSince.current - lastInput < 5 * 60000
        const waitingForKey = !!analysisRef.current?.action || analysisRef.current?.state === 'gameplay'
        if (info && sig.startedAt && !rescueShown.current && !resumedRef.current && now2 - sig.startedAt > 40000
          && staticSince.current && now2 - staticSince.current > 45000 && lastInput < staticSince.current
          && frozeSoonAfter && !waitingForKey && !(save?.exists && !resumeAsked)) {
          rescueShown.current = true
          setRescue('now')
          autoReport('hangs')
        }
      }
      if (sig.started && info && !sentPlay.current) {
        sentPlay.current = true
        playStart.current = Date.now()
        api.tasteEvent('play_browser', info.id).catch(() => {})
      }
      const conf = gameplayConfidence(sig, tracker.current.seen, profile)
      const padConnected = sig.pads.some((p) => p.player)   // a controller you have actually used
      setSignals(sig)
      setConfidence(conf)
      // What is the game waiting for?
      let sc: ScreenSample | null = null
      let inputs: { k: string; t: number }[] = []
      try { sc = player()?.screen() ?? null; inputs = player()?.recentInputs() ?? [] } catch { /* player restarting */ }
      const now = Date.now()
      if (sc?.basicBanner && sc.lastLine === 'READY.') { if (!basicSince.current) basicSince.current = now } else basicSince.current = 0
      const a = analyze(sc, sig, info?.inputProfile ?? null, {
        gameplay: conf.likely || (pref === 'auto' && mode === 'play'),  // (a key prompt on screen still wins)
        nextStep: stepsDone.current,
        basicSince: basicSince.current ? now - basicSince.current : 0,
        elapsed: sig.startedAt ? (now - sig.startedAt) / 1000 : 0,
        title: info?.title,
      })
      setScreen(sc)
      setAnalysis(a)
      setRecentKeys(inputs.slice(-8))
      if (a.signature !== sigSince.current.sig) sigSince.current = { sig: a.signature, t: now }
      if (a.state === 'gameplay' && info?.issues?.length && !workedSent.current) {
        workedSent.current = true
        api.gameWorked(info.id).catch(() => {}) // it works now: noted on the open reports
      }
      const learned = learner.current.update(a, inputs, now)
      if (learned) stepsDone.current += 1
      // Reached the game: keep the keys that got us here (for this exact file) for next time.
      if (a.state === 'gameplay' && info?.inputProfile?.sha256 && learner.current.steps.length) {
        const seq = JSON.stringify(learner.current.steps)
        if (seq !== savedLearned.current) {
          savedLearned.current = seq
          api.learnInputProfile(info.id, { sha256: info.inputProfile.sha256, startupSequence: learner.current.steps })
            .then((p) => { setInfo((i) => (i ? { ...i, inputProfile: p } : i)); toast(`📝 Remembered how to start ${info.title}: ${learner.current.steps.map((st) => keyLabel(st.key)).join(' → ')}`, 'ok') })
            .catch(() => {})
        }
      }
      // Auto start (setting): press obvious startup keys when the reading is very sure.
      const act = a.action
      const recentlyTyped = sig.lastKeyAt && now - sig.lastKeyAt < 2000
      const choosingSave = (save?.exists && !resumeAsked) || resumedRef.current  // your choice first; resumed = already past the start
      if (autoStart && act?.auto && act.confidence >= 90 && a.state !== 'gameplay' && !recentlyTyped && !choosingSave
        && now - sigSince.current.t > 1500 && !(info?.smartStart?.auto && info.smartStart.source === 'recorded')) {
        const tag = `${a.signature}|${act.key}`
        if (!sentFor.current.has(tag)) {
          sentFor.current.add(tag)
          stepsDone.current += 1
          player()?.playStep(guideStep(act.key))
          toast(`⏩ Auto start: pressed ${keyLabel(act.key)} — ${act.reason}`, 'ok')
        }
      }
      const lostPad = hadPad.current && !padConnected
      hadPad.current = padConnected
      // Controller unplugged while playing with it: keyboard controls again (Type).
      const effPref: InputPref = lostPad && pref === 'play' ? 'auto' : pref
      if (lostPad) {
        toast('Controller disconnected — keyboard controls enabled.', 'ok')
        if (pref === 'play') setPref('auto')
      }
      setMode((current) => {
        // Back at the BASIC prompt (game quit / reset): the keyboard is for typing again.
        // A start / option screen again (next level's "press fire", a menu): startup keys again.
        const next = pref === 'auto' && (a.state === 'basic' || (a.prompt && a.action?.source === 'screen' && a.action.auto) || a.state === 'trainer') ? 'type'
          : decideMode(effPref, padConnected || !!sig.lastJoyKeyAt, conf.likely, lostPad ? 'type' : current)
        if (current === 'type' && next === 'play' && pref === 'auto') {
          toast(padConnected ? '🎮 Game started — your controller is in charge (Play mode)'
            : '🕹 Game started — arrows = joystick, Space = fire (Ctrl+Alt+G to type)', 'ok')
          if (info && sig.startedAt && !profile?.gameplayAfter) {
            setProfile(saveProfile(info.id, { gameplayAfter: Math.round((Date.now() - sig.startedAt) / 1000) }))
          }
        }
        return next
      })
    }, 500)
    return () => window.clearInterval(t)
  }) // re-created each render: always sees the current pref/profile

  // Ctrl+Alt+G (from the player or this page) switches Type ⇄ Play manually.
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      if (e.data?.type === 'c64-toggle-mode') choose(mode === 'play' ? 'type' : 'play')
      if (e.data?.type === 'c64-port-toggle') {
        choosePort(port === 1 ? '2' : '1')
        toast(`🕹 Joystick now in port ${port === 1 ? 2 : 1}`, 'ok')
      }
      if (e.data?.type === 'c64-load-error') autoReport('wont_load', { loadError: String(e.data.text || '') })
      if (e.data?.type === 'c64-esc' && !escHinted.current) {
        escHinted.current = true
        toast('Esc stays with your browser. RUN/STOP is Ctrl+R (or the RUN/STOP button) · Shift+Esc leaves the game', 'ok')
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  })

  useEffect(() => {
    api.emulatorFiles(Number(id)).then((r) => {
      const i = r as EmuInfo
      setInfo(i)
      if (i.hangs) setRescue('before') // this version got stuck before (hang detection keeps running: repeats are logged)
    }).catch((e) => {
      setError(errorMessage(e))
      api.reportIssue({ gameId: Number(id), category: 'wont_load', source: 'auto', diagnostics: { loadError: errorMessage(e), userAgent: navigator.userAgent } }).catch(() => {})
    })
    api.saveInfo(Number(id)).then(setSave).catch(() => setSave({ exists: false }))
    // Switching to 💻 In browser: stop whatever the real C64 is playing, so both don't run at once.
    api.handoffToBrowser()
      .then((r) => { if (r.reset) toast(`📺 Your C64 was reset (stopped ${r.title}) — now playing 💻 in the browser`, 'ok') })
      .catch(() => {}) // C64 offline or unreachable: nothing to stop
    setResumeAsked(false)
    sentPlay.current = false
    playStart.current = 0
    resumedRef.current = false
    autoRanRef.current = false
    setGuideStepIx(0)
    setRecording(false)
    learner.current.reset()
    stepsDone.current = 0
    sentFor.current = new Set()
    savedLearned.current = ''
    setAnalysis(null)
    setPanel(null)
    setRescue(null)
    autoReported.current = new Set()
    workedSent.current = false
    staticSince.current = 0
    rescueShown.current = false
    // A new game: back to the safe default and Auto; load what we learned about this game.
    setPref('auto')
    setMode('type')
    setPortPref('auto')
    tracker.current.reset()
    setProfile(loadProfile(Number(id)))
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (n: number) => {
    if (n === file) return
    // Multi-disk bundle: swap the disk in the running game, like inserting the next disk.
    if (info?.bundleUrl && (player()?.disk(n) ?? 0) > 0) {
      setFile(n)
      focusGame()
      return
    }
    if (!window.confirm('Switching file restarts the emulator from that file. Continue?')) return
    try { player()?.allowUnload() } catch { /* player gone */ }
    setFile(n)
    setMode('type')
    tracker.current.reset()
    learner.current.reset()
    stepsDone.current = 0
    setFast(false)
    setNonce(Date.now())
  }

  // Text adventures: by genre / style tags, or a text screen read by the C64's own keyboard routine.
  const looksAdventure = /adventure/i.test(info?.genre ?? '') || (info?.styleTags ?? []).includes('keyboard needed')
    || (!!screen?.kernalIrq && screen.mode === 'text' && (analysis?.state === 'menu' || screen.staticText.length > 300))

  // ---- context-aware help: what matters on this screen, in PC keys
  const joyNow = pref === 'play' || (pref !== 'type' && (mode === 'play' || autoKeymap?.arrows === 'joy'))
  const spaceNow = pref === 'play' || (pref === 'auto' && mode === 'play') ? 'fire' : pref === 'type' ? 'space' : autoKeymap?.space ?? 'space'
  const controls = info?.inputProfile?.controls ?? {}
  const ctlText = Object.entries(controls).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(' · ')
  const st = analysis?.state
  const nextStep = analysis?.action && st !== 'gameplay' && st !== 'loading' && pref !== 'play' ? analysis.action : null
  const startWord = st === 'basic' ? 'Start the program' : st === 'trainer' || st === 'menu' ? 'Continue'
    : /^[1-4]$/.test(analysis?.action?.key ?? '') ? 'Players' : 'Start'
  const pads = signals?.pads.filter((p) => p.player) ?? []
  const portHint = portPref === 'auto' && analysis?.port && analysis.port.confidence >= 70 && analysis.port.port !== port ? analysis.port : null
  const title = info?.title ? `${info.title} — ` : ''
  const helpLine = (() => {
    if (pref === 'type') return `⌨ Type: the whole keyboard is the C64 keyboard (arrows = cursor keys) · Ctrl+R = RUN/STOP · Ctrl = C= · Ctrl+Alt+G = Play · Shift+Esc = leave`
    if (st === 'loading') return `${title}Loading… · Shift+Esc = leave`
    if (st === 'basic') return `${title}BASIC: type normally · Enter = RETURN · Ctrl+R = RUN/STOP · Shift+Esc = leave`
    if (st === 'gameplay' || pref === 'play') {
      return `${title}${ctlText ? ctlText + ' · ' : ''}Arrows = move · Space = fire${pads.length ? ` · 🎮 ${pads.length > 1 ? `${pads.length} controllers` : 'controller'}` : ''} · Joystick port ${port}`
        + ` · other keys type · Ctrl+Alt+G = keyboard mode · Shift+Esc = leave`
    }
    const parts: string[] = []
    if (st === 'trainer') parts.push('Trainer / options: Y / N = choose · Enter = RETURN')
    else if (st === 'menu') parts.push(analysis?.hints[0]?.startsWith('Y / N') ? 'Question: Y / N = answer · type + Enter' : 'Menu: number keys pick · Enter = RETURN')
    if (st !== 'trainer' && st !== 'menu') for (const h of analysis?.hints ?? []) parts.push(h)
    parts.push(joyNow ? 'Arrows = joystick' : 'Arrows = cursor keys')
    parts.push(spaceNow === 'fire' ? 'Space = fire' : spaceNow === 'both' ? 'Space = space bar, then fire' : 'Space = space bar')
    parts.push('Ctrl+R = RUN/STOP', 'Enter = RETURN', 'F1–F8 = buttons above', 'Ctrl+Alt+G = Play/Type', 'Shift+Esc = leave')
    return `${title}${parts.join(' · ')}`
  })()

  return (
    <div className={`emu-page ${tvMode ? 'emu-tv' : ''}`}>
      <header className="emu-bar">
        <button className="btn btn-ghost btn-sm" onClick={leave} title="Leave the game (Shift+Esc)">← Back</button>
        <span className="mode-pill mode-browser" title="This game runs in an emulator on this device. Your C64 Ultimate is not used.">💻 In browser</span>
        <strong className="emu-title">{info?.title ?? 'Loading…'}</strong>
        {info && <Thumbs title={info.title} gameId={info.id} compact />}
        {info && <button className="btn btn-ghost btn-sm" onClick={() => setReporting(true)}
          title="⚑ Report a problem with this game (saved with diagnostics in the compatibility log)">⚑ Report</button>}
        {status?.connected && info && (
          <button className="btn btn-ghost btn-sm hide-sm" title="Load this game on your real Commodore 64 Ultimate instead"
            onClick={() => api.play(info.id).then(() => navigate('/stream')).catch((e) => setError(errorMessage(e)))}>📺 Play on my C64 instead</button>
        )}
        {info && info.files.length > 1 && (
          <select value={file} onChange={(e) => pick(Number(e.target.value))} aria-label="Disk or file"
            title={info.bundleUrl ? 'Insert another disk (the game keeps running) — when the game asks for disk 2 / side B, pick it here' : 'Start another file'}>
            {info.files.map((f, n) => <option key={f.mediaId} value={n}>Disk {f.disk} · {f.name}</option>)}
          </select>
        )}
        <div className="seg" role="group" aria-label="Input mode">
          <button className={`seg-btn ${pref === 'auto' ? 'on' : ''}`} onClick={() => choose('auto')}
            title="Starts in Type for intros and menus; switches to Play by itself once a controller is connected and the game has started">
            Auto{pref === 'auto' ? (typing ? ' · ⌨' : ' · 🎮') : ''}</button>
          <button className={`seg-btn ${pref === 'type' ? 'on' : ''}`} onClick={() => choose('type')}
            title="The whole keyboard is the C64 keyboard — arrows are the cursor keys, Space is the space bar (Ctrl+Alt+G switches)">⌨ Type</button>
          <button className={`seg-btn ${pref === 'play' ? 'on' : ''}`} onClick={() => choose('play')}
            title="Arrows = joystick, Space = fire; every other key still types on the C64 (Y/N, Enter, F-keys). Ctrl+Alt+G switches">🎮 Play</button>
        </div>
        {recording ? (
          <button className="btn btn-sm emu-rec" onClick={stopRecording}
            title="Stop and save: next time this game starts in the browser, these keys are pressed for you">⏹ Stop recording</button>
        ) : (
          <button className="btn btn-ghost btn-sm" onClick={startRecording}
            title="Smart start: record the keys and fire presses that get you past the intro / trainer / menu, once">⏺ Record start</button>
        )}
        {info?.smartStart?.source === 'recorded' && !recording && (
          <button className="btn btn-ghost btn-sm" onClick={replayStart} title={`Replay the recorded start (${info.smartStart.steps.length} steps). Shift+click to delete it.`}
            onContextMenu={(e) => { e.preventDefault(); forgetStart() }}>⏩ Smart start</button>
        )}
        {!info?.smartStart && guideKeys.length > 0 && guideStepIx < guideKeys.length && (
          <button className="btn btn-sm emu-next" onClick={nextGuideKey}
            title={`From the game's guide: ${info?.guide?.start.join(' → ')}`}>⏭ Next: {guideKeys[guideStepIx]} ({guideStepIx + 1}/{guideKeys.length})</button>
        )}
        <button className={`btn btn-sm ${panel === 'netplay' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPanel(panel === 'netplay' ? null : 'netplay')}
          title="👥 Play together: a friend joins from their own device as player 2 (they see and hear this game)">👥 Invite</button>
        <button className={`btn btn-sm ${panel === 'hint' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPanel(panel === 'hint' ? null : 'hint')}
          title="💡 Stuck? Spoiler-free help for what's on screen — a nudge first, the solution only if you ask">💡 Stuck?</button>
        {looksAdventure && (
          <button className={`btn btn-sm ${panel === 'copilot' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPanel(panel === 'copilot' ? null : 'copilot')}
            title="🧭 Text adventure co-pilot: tracks room, exits and inventory and suggests commands to type">🧭 Co-pilot</button>
        )}
        {keyBtn('runstop', 'RUN/STOP', 'C64 RUN/STOP key (keyboard: Ctrl+R). Hold it and tap RESTORE to stop most programs.')}
        {keyBtn('commodore', 'C=', 'Commodore key (keyboard: Ctrl). In typing mode, C= + SHIFT switches upper/lower case.')}
        {keyBtn('restore', 'RESTORE', 'C64 RESTORE key (keyboard: Page Up)')}
        <span className="emu-fkeys" role="group" aria-label="Function keys">
          {['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8'].map((f) => {
            const n = Number(f.slice(1))
            const tip = n % 2 ? `C64 ${f} (keyboard: ${f})` : `C64 ${f} = SHIFT + F${n - 1} (keyboard: ${f} or Shift+F${n - 1})`
            return <span key={f}>{keyBtn(f, f, tip)}</span>
          })}
        </span>
        <select className="emu-port" value={String(portPref)} onChange={(e) => choosePort(e.target.value)} aria-label="Joystick port"
          title="Which C64 joystick port your controller and the arrow keys use (Ctrl+Alt+P swaps). Auto = what you chose last time for this game, else what the game shows or reads, else its known port, else port 2.">
          <option value="auto">Port: Auto ({autoPort})</option>
          <option value="1">Port 1</option>
          <option value="2">Port 2</option>
        </select>
        <button className={`btn btn-sm ${showKeys ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setShowKeys((v) => !v)}
          title="Which PC key is which C64 key">⌨ Keys</button>
        <button className="btn btn-ghost btn-sm" onClick={saveNow} disabled={saving}
          title="Save your progress on the console — resume later on this or any other device (leaving the game also saves)">
          {saving ? '💾 Saving…' : '💾 Save'}</button>
        {save?.exists && resumeAsked && (
          <button className="btn btn-ghost btn-sm" onClick={resume} title="Go back to your last save">⟲ Resume</button>
        )}
        {fps > 0 && (
          <span className={`badge ${fps >= 48 ? 'badge-supported' : fps >= 40 ? 'badge-unverified' : 'badge-unsupported'}`}
            title="Emulation speed. A PAL C64 runs at 50 frames per second; below that, music and games play slowly.">
            {Math.min(100, Math.round(fps * 2))}% speed
          </span>
        )}
        {fps > 0 && fps < 48 && !fast && (
          <button className="btn btn-sm" onClick={() => { try { player()?.allowUnload() } catch { /* gone */ } setFast(true); setMode('type'); tracker.current.reset(); setNonce(Date.now()) }}
            title="Restart with lighter sound emulation (FastSID) so slower devices keep up">⚡ Fast mode</button>
        )}
      </header>
      {showKeys && (
        <div className="emu-keys" role="dialog" aria-label="Keyboard guide">
          <table>
            <thead><tr><th>C64</th><th className={!typing ? 'on' : ''}>🕹 Play</th><th className={typing ? 'on' : ''}>⌨ Type</th></tr></thead>
            <tbody>
              {KEYS.map(([c64, joy, type]) => (
                <tr key={c64}><td>{c64}</td><td className={!typing ? 'on' : ''}>{joy}</td><td className={typing ? 'on' : ''}>{type}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Gamepad: D-pad = joystick, A/B/Y = fire, X = space bar,
            Start = RETURN, shoulders = F1/F7, triggers = F3/F5, stick clicks = RESTORE / C=.
            Phones and tablets show touch controls. Plain Esc belongs to your browser (e.g. leaving full screen);
            Shift+Esc leaves the game. Ctrl is the C= key, but the browser keeps Ctrl+W / Ctrl+T / Ctrl+N —
            use the on-screen C= button for those combinations.</p>
          <label className="toggle small"><input type="checkbox" checked={autoStart} onChange={(e) => {
            setAutoStart(e.target.checked)
            try { localStorage.setItem('c64.autoStart', e.target.checked ? '1' : '0') } catch { /* ignore */ }
          }} /> Automatically handle known game startup screens (presses the start key when the screen clearly asks for it, or it worked before)</label>
          <label className="toggle small"><input type="checkbox" checked={escRunstop} onChange={(e) => {
            setEscRunstop(e.target.checked)
            try { localStorage.setItem('c64.escRunstop', e.target.checked ? '1' : '0') } catch { /* ignore */ }
          }} /> Esc = RUN/STOP too (classic; Esc then no longer leaves full screen)</label>
          {info?.inputProfile?.startupSource === 'learned' && (
            <button className="btn btn-ghost btn-sm" onClick={async () => {
              await api.forgetInputProfile(info.id).catch(() => {})
              setInfo({ ...info, inputProfile: { ...info.inputProfile!, startupSequence: [], startupSource: null } })
              learner.current.reset()
              savedLearned.current = ''
              toast('Forgot how this game starts', 'ok')
            }}>Forget the learned start keys ({info.inputProfile.startupSequence.map((st) => keyLabel(st.key)).join(' → ')})</button>
          )}
          <label className="toggle small"><input type="checkbox" checked={debug} onChange={(e) => {
            setDebug(e.target.checked)
            try { localStorage.setItem('c64.inputDebug', e.target.checked ? '1' : '0') } catch { /* ignore */ }
          }} /> Show input diagnostics (for tuning Auto mode)</label>
          <button className="btn btn-sm" onClick={() => { setShowKeys(false); focusGame() }}>Close</button>
        </div>
      )}
      {save?.exists && !resumeAsked && (
        <div className="emu-resume" role="dialog" aria-label="Resume your game">
          {save.hasThumb && <img src={`/api/games/${id}/save/thumb?t=${save.savedAt}&profile=${currentProfile()}`} alt="" />}
          <div>
            <strong>Continue where you left off?</strong>
            <p className="muted small">
              {save.kind === 'auto' ? 'Saved automatically' : 'Saved'} {save.savedAt ? timeAgo(save.savedAt) : ''}
              {save.device ? ` on ${save.device}` : ''}.
            </p>
            <div className="row-actions">
              <button className="btn btn-primary" onClick={resume}>⟲ Resume</button>
              <button className="btn" onClick={startFresh}>Start fresh</button>
            </div>
          </div>
        </div>
      )}
      {reporting && info && <ReportProblem gameId={info.id} title={info.title} collect={() => collectDiagnostics(true)} onClose={() => { setReporting(false); focusGame() }} />}
      {!rescue && info?.issues && info.issues.length > 0 && (
        <div className="emu-issue-note small">🐞 Reported before in the browser: {info.issues.map((i) => `${i.category}${i.occurrences > 1 ? ` (${i.occurrences}×)` : ''}`).join(', ')}
          {' · '}<button className="linklike" onClick={() => navigate(`/compatibility?game=${info.id}`)}>see the log</button></div>
      )}
      {rescue && info && (
        <HangRescue gameId={info.id} before={rescue === 'before'}
          onNudge={() => { player()?.playStep({ key: 'FIRE+SPACE' }); setRescue(null); focusGame() }}
          onDismiss={() => { setRescue(null); focusGame() }}
          onLeave={() => { flushSession(); try { player()?.allowUnload() } catch { /* gone */ } }} />
      )}
      {panel === 'netplay' && info && (
        <NetplayHost gameId={info.id} port={port} onClose={() => { setPanel(null); focusGame() }}
          player={() => player() as unknown as { stream(): MediaStream | null; rtc(c: RTCConfiguration): RTCPeerConnection; remoteJoy(st: Record<string, boolean>): void; playStep(s: SmartStep): Promise<boolean> }} />
      )}
      {panel === 'hint' && info && <HintPanel gameId={info.id} screen={() => screen} onClose={() => { setPanel(null); focusGame() }} />}
      {panel === 'copilot' && info && (
        <Copilot gameId={info.id} screen={() => { try { return player()?.screen() ?? null } catch { return null } }}
          type={async (t) => { const ok = await (player()?.type(t) ?? Promise.resolve(false)); focusGame(); return ok }}
          onClose={() => { setPanel(null); focusGame() }} />
      )}
      <div className="emu-help muted small">
        {helpLine}
        {(score.metrics.score !== undefined || score.metrics.level !== undefined) && (
          <span className="score-chip" title="Read from the game's own screen — counts for achievements and high scores">🏆 {score.metrics.score !== undefined ? score.metrics.score.toLocaleString() : ''}{score.metrics.level !== undefined ? ` · round ${score.metrics.level}` : ''}{score.metrics.lives !== undefined ? ` · ${score.metrics.lives} lives` : ''}{score.best ? ` · new best ${score.best.toLocaleString()}` : ''}</span>
        )}
        {nextStep && (
          <button className="btn btn-sm emu-next emu-start" onClick={() => { player()?.playStep(guideStep(nextStep.key)); stepsDone.current += 1; focusGame() }}
            title={`${nextStep.reason} — click to press it`}>▶ {startWord}: press <b>{pcKey(nextStep.key, analysis?.keymap)}</b>{nextStep.key !== 'RUN' && pcKey(nextStep.key) !== keyLabel(nextStep.key) ? ` (${keyLabel(nextStep.key)})` : ''}</button>
        )}
        {portHint && (
          <button className="btn btn-sm emu-next" onClick={() => choosePort(String(portHint.port))} title={portHint.reason}>
            🕹 Joystick port {portHint.port} appears to be needed — switch (Ctrl+Alt+P)</button>
        )}
        {pref === 'auto' && typing && !signals?.pads.some((p) => p.player) && confidence?.likely && (
          <span className="emu-hint"> · 🎮 Game running? Press <b>Play</b> (Ctrl+Alt+G): arrows + Space become the joystick</span>
        )}
      </div>
      {debug && (
        <div className="emu-diag" aria-label="Input detection diagnostics">
          <strong>INPUT DETECTION</strong>
          <div>Controllers: {signals?.pads.length ? signals.pads.map((p) => `${p.id.split('(')[0].trim()} (#${p.index}) → ${p.player ? `player ${p.player}` : 'not used yet'}`).join(' · ') : 'none'}</div>
          <div>Mode: {mode.toUpperCase()} · preference {pref.toUpperCase()} · port {port}{portPref === 'auto' ? ' (auto)' : ''}</div>
          <div>Gameplay confidence: {confidence?.score ?? 0}% {confidence?.likely ? '→ gameplay' : ''}</div>
          {confidence?.signals.map((sg) => <div key={sg.key}>{sg.on ? '✓' : '·'} {sg.label} (+{sg.points})</div>)}
          <div>Screen motion: {signals?.motion.map((m) => Math.round(m * 100)).join(' ') || '—'} · {signals?.fps ?? 0} fps</div>
          <div>Profile: {profile ? JSON.stringify({ gameplayAfter: profile.gameplayAfter, joystickPort: profile.joystickPort }) : 'none yet'}</div>
          <strong>STARTUP ANALYZER</strong>
          <div>Game: {info?.title} · profile: {info?.inputProfile ? `${info.inputProfile.startupSource ?? 'no start keys'}${info.inputProfile.startupSequence.length ? ` [${info.inputProfile.startupSequence.map((x) => x.key).join(' → ')}]` : ''} · port ${info.inputProfile.joystickPort ?? '?'} (${info.inputProfile.joystickPortSource ?? '—'}) · sha256 ${info.inputProfile.sha256?.slice(0, 10) ?? '—'}` : 'loading'}</div>
          <div>State: {analysis?.state ?? '—'} · keys: arrows {autoKeymap?.arrows ?? (typing ? 'cursor' : 'joy')}, space {spaceNow}{escRunstop ? ' · Esc = RUN/STOP' : ''} · auto start {autoStart ? 'on' : 'off'}</div>
          <div>Start action: {analysis?.action ? `${analysis.action.key} (${analysis.action.confidence}%, ${analysis.action.source}${analysis.action.auto ? ', auto-safe' : ''}) — ${analysis.action.reason}` : 'none'}</div>
          <div>Port guess: {analysis?.port ? `${analysis.port.port} (${analysis.port.confidence}%) — ${analysis.port.reason}` : 'none'} · in use {port}</div>
          {analysis?.reasons.map((r, i) => <div key={i}>· {r}</div>)}
          <div>Screen: {screen ? `${screen.mode} @ $${screen.base?.toString(16) ?? '?'} · sample #${screen.n} · KERNAL keyboard ${screen.kernalIrq ? 'on' : 'off'}` : 'not read yet'}</div>
          <div>Signature: {analysis?.signature || '—'}</div>
          {screen?.staticText && <div className="emu-diag-text">Text: {screen.staticText.slice(0, 220)}</div>}
          {screen?.scrollText && <div className="emu-diag-text">Scroller: …{screen.scrollText.slice(-160)}</div>}
          <div>Joystick reads in program: $DC00 (port 2) ×{screen?.ports.dc00Reads ?? 0} · $DC01 (port 1 / keyboard) ×{screen?.ports.dc01Reads ?? 0} · keyboard scan writes ×{screen?.ports.dc00Writes ?? 0}</div>
          <div>Recent input: {recentKeys.map((x) => x.k).join(' ') || '—'} · learned this session: {learner.current.steps.map((x) => `${x.key}@"${x.when.slice(0, 24)}"`).join(' → ') || '—'}</div>
        </div>
      )}
      {error
        ? <div className="emu-error">{error}</div>
        : info && (
          <iframe ref={frame} key={`${file}-${nonce}`} className="emu-frame" title={`${info.title} (emulator)`}
            src={`/emulator/play.html?id=${info.id}&file=${info.bundleUrl ? 0 : file}${fast ? '&fast=1' : ''}`}
            onLoad={() => window.setTimeout(focusGame, 300)}
            allow="autoplay; fullscreen; gamepad; clipboard-write" allowFullScreen />
        )}
    </div>
  )
}

function timeAgo(t: number): string {
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`
  if (s < 86400 * 2) return `${Math.round(s / 3600)} hours ago`
  return `on ${new Date(t * 1000).toLocaleDateString()}`
}
