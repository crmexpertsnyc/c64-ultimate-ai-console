/**
 * Startup input analyzer for Browser Play: what is the game waiting for, and which keys should do what?
 *
 * Works from what the C64 really shows — the player reads screen memory from the emulator (no OCR):
 * the text on screen, text-vs-picture mode, scrolling text stitched together, the BASIC loader lines,
 * and which joystick registers the program reads. On top of that, per-game knowledge: startup keys
 * learned on this console (tied to the exact file), a small built-in list and the researched guide.
 *
 * Nothing here is game specific: Bubble Bobble ("[SPACE] to read or [RUN/STOP] to start") is simply one
 * of the screens these rules understand.
 */

import type { InputSignals } from './autoInput'

export type GameState = 'loading' | 'basic' | 'intro' | 'title' | 'trainer' | 'menu' | 'gameplay' | 'unknown'

/** Startup keys (same names as the console's profiles and guides). */
export type StartKey = 'FIRE' | 'SPACE' | 'FIRE+SPACE' | 'RUN/STOP' | 'RETURN' | 'F1' | 'F3' | 'F5' | 'F7'
  | 'Y' | 'N' | 'RUN' | 'ANY' | 'RESTORE' | 'UP' | 'DOWN' | 'LEFT' | 'RIGHT' | string

export interface ScreenSample {
  at: number
  n: number
  mode: 'text' | 'bitmap' | 'blank'
  base?: number             // screen memory address
  rows: string[]
  rawRows?: string[]         // 40 columns, positions kept (numbers under their labels)
  staticText: string
  scrollText: string
  movingRows: number
  basicBanner: boolean
  lastLine: string
  kernalIrq: boolean
  ports: { dc00Reads: number; dc01Reads: number; dc00Writes: number }
}

export interface InputProfileInfo {
  sha256: string | null
  startupSequence: { key: string; when?: string }[]
  startupSource: 'learned' | 'builtin' | 'guide' | null
  joystickPort: number | null
  joystickPortSource: 'learned' | 'builtin' | 'guide' | 'library' | null
  controls: Record<string, string>
}

export interface Suggestion {
  key: StartKey
  confidence: number        // 0–100, internal (shown only in diagnostics)
  reason: string
  source: 'screen' | 'scroller' | 'learned' | 'builtin' | 'guide' | 'basic'
  auto: boolean             // safe to press automatically when "Auto start" is on
}

export interface PortGuess { port: 1 | 2; confidence: number; reason: string }

export interface Keymap { arrows: 'joy' | 'cursor'; space: 'fire' | 'space' | 'both' }

export interface Analysis {
  state: GameState
  signature: string         // what this screen "is", for learning ("(picture)", a prompt line, the first text)
  prompt: string | null     // the on-screen line the action comes from
  action: Suggestion | null
  hints: string[]           // other keys that matter on this screen ("Y/N = choose", "1-4 = pick")
  port: PortGuess | null
  keymap: Keymap            // for Auto, before gameplay
  reasons: string[]         // for the diagnostics overlay
}

// ---- screen text rules

const KEY = String.raw`(FIRE ?BUTTON|FIRE|BUTTON|SPACE ?BAR|SPACE|RUN ?\/? ?STOP|RETURN|ENTER|F ?[1357]|ANY KEY)`
const START_RE = new RegExp(String.raw`(?:(?:PRESS|HIT|PUSH|USE)\s+)?(?:THE\s+)?${KEY}(?:\s+KEY)?\s+(?:TO|FOR)\s+(?:START|PLAY|BEGIN|GAME|CONTINUE|PROCEED|GO ON|SKIP|LOAD)`)
const PRESS_RE = new RegExp(String.raw`(?:PRESS|HIT|PUSH)\s+(?:THE\s+)?${KEY}`)
const ANY_KEY_RE = /PRESS\s+(?:ANY|A)\s+KEY/
const YN_RE = /\bY\s*\/\s*N\b|\(\s*Y\s*\/\s*N\s*\)|\bYES\s*\/\s*NO\b/
// A yes/no question ("WANT TO RESTORE PREVIOUSLY SAVED GAME?") — typical of text adventures and menus.
const QUESTION_RE = /\b(?:WANT TO|DO YOU|WOULD YOU|ARE YOU|SHALL I|RESTORE|LOAD A? ?SAVED)\b[^/?]*\?/
const TRAINER_RE = /TRAINER|UNLIMITED|INFINITE|CHEAT|\bLIVES\b.*\b(?:Y|N)\b/
const MENU_RE = /(?:^|\/ )\s*[1-4]\s*[-.:)]\s*[A-Z]{3}/
// "PRESS 1 OR 2 TO PLAY" (games with their own font may show the digits as other glyphs: take the first).
const PLAYERS_RE = /PRESS\s+(\S)\s+OR\s+(\S)\s+(?:TO\s+PLAY|PLAYERS?|FOR)/
const PORT_RE = /(?:JOYSTICK|JOY|STICK)?\s*(?:IN\s+)?PORT\s*#?\s*([12])\b/
const OTHER_PLAYER_RE = /(PLAYER\s*(?:2|TWO)|2ND\s+PLAYER|SECOND\s+PLAYER)[^/]{0,24}$/

export function keyName(raw: string): StartKey {
  const k = raw.replace(/\s+/g, ' ').trim()
  if (/^(FIRE|BUTTON|FIRE ?BUTTON)$/.test(k)) return 'FIRE'
  if (/^SPACE/.test(k)) return 'SPACE'
  if (/^RUN/.test(k)) return 'RUN/STOP'
  if (k === 'RETURN' || k === 'ENTER') return 'RETURN'
  if (/^F ?[1357]$/.test(k)) return 'F' + k.slice(-1)
  if (k === 'ANY KEY') return 'ANY'
  return k
}

/** The PC key(s) for a C64 key, for the help line and buttons. */
export function pcKey(k: StartKey, keymap?: Keymap): string {
  switch (k) {
    case 'RUN/STOP': return 'Ctrl+R'
    case 'FIRE': return keymap?.space === 'space' ? 'Fire (gamepad)' : 'Space / Fire'
    case 'FIRE+SPACE': return 'Space'
    case 'SPACE': return 'Space'
    case 'ANY': return 'Space'
    case 'RETURN': return 'Enter'
    case 'RUN': return 'type RUN + Enter'
    case 'RESTORE': return 'Page Up'
    default: return k
  }
}

/** What a C64 key is called in plain words. */
export function keyLabel(k: StartKey): string {
  return k === 'FIRE+SPACE' ? 'Space / Fire' : k === 'ANY' ? 'any key' : k === 'RUN' ? 'RUN' : k
}

/** A line of real words (not graphic characters that happen to decode as letters: "KKKKXXKIKKKK"). */
export function wordy(line: string): boolean {
  if (/(.)\1\1/.test(line.replace(/[\s*=\-.]/g, ''))) return false
  const words = line.match(/\b[A-Z]{2,}\b/g) ?? []
  return words.some((w) => w.length >= 3 && /[AEIOU]/.test(w)) && words.filter((w) => /[AEIOUY]/.test(w)).length >= Math.ceil(words.length / 2)
}

function lineWith(text: string, index: number): string {
  const start = text.lastIndexOf(' / ', index)
  const end = text.indexOf(' / ', index)
  return text.slice(start < 0 ? 0 : start + 3, end < 0 ? undefined : end).trim().slice(0, 80)
}

function findPrompt(text: string, title = ''): { key: StartKey; line: string; strong: boolean } | null {
  const s = START_RE.exec(text)
  if (s) return { key: keyName(s[1]), line: lineWith(text, s.index), strong: true }
  const a = ANY_KEY_RE.exec(text)
  if (a) return { key: 'ANY', line: lineWith(text, a.index), strong: true }
  const n = PLAYERS_RE.exec(text)
  // How many players is your choice: suggested, never pressed automatically.
  if (n) return { key: /[1-4]/.test(n[1]) ? n[1] : '1', line: lineWith(text, n.index), strong: false }
  const p = PRESS_RE.exec(text)
  if (p) {
    // "PRESS FIRE FOR BUBBLE BOBBLE": pressing it starts this very game.
    const word = title.toUpperCase().split(/[^A-Z0-9]+/).find((w) => w.length >= 4)
    const line = lineWith(text, p.index)
    const strong = !!word && new RegExp(String.raw`${KEY}\s+(?:FOR|TO PLAY)\s+(?:THE\s+)?${word}`).test(line)
    return { key: keyName(p[1]), line, strong }
  }
  return null
}

function screenPort(text: string): 1 | 2 | null {
  const m = PORT_RE.exec(text)
  if (!m) return null
  if (OTHER_PLAYER_RE.test(text.slice(0, m.index))) return null // "player 2 … port 1": not your port
  return m[1] === '1' ? 1 : 2
}

/** A stable name for the current screen, used to learn "on this screen, that key worked". */
export function screenSignature(screen: ScreenSample | null): string {
  if (!screen) return ''
  if (screen.mode === 'bitmap') return '(picture)'
  if (screen.mode === 'blank') return '(blank)'
  const prompt = findPrompt(screen.staticText)
  if (prompt) return prompt.line
  const words = screen.staticText.split(' / ').filter((l) => wordy(l) && !l.includes('BASIC V2') && !l.includes('BYTES FREE'))
  // No readable words (a screen drawn with graphic characters): one generic name, like a picture.
  return words.length ? words.join(' / ').slice(0, 60) : '(graphics)'
}

function matches(when: string | undefined, screen: ScreenSample, signature: string): boolean {
  if (!when) return false
  if (when === '(picture)') return screen.mode === 'bitmap'
  if (when === '(blank)') return screen.mode === 'blank'
  if (when === '(graphics)') return screen.mode === 'text' && signature === '(graphics)'
  return signature === when || screen.staticText.includes(when)
}

export interface AnalyzeContext {
  gameplay: boolean      // Auto's gameplay detection (autoInput.gameplayConfidence) says the game is running
  nextStep: number       // how many profile steps have been done this session
  basicSince: number     // ms the screen has shown READY. after a load (0 = not)
  elapsed: number        // seconds since the game booted
  title?: string         // the game's name ("press fire for <name>" starts it)
}

export function analyze(screen: ScreenSample | null, sig: InputSignals | null, profile: InputProfileInfo | null,
  ctx: AnalyzeContext): Analysis {
  const reasons: string[] = []
  const hints: string[] = []
  const signature = screenSignature(screen)
  let state: GameState = 'unknown'
  let action: Suggestion | null = null
  let prompt: string | null = null
  const text = screen?.mode === 'text' ? screen.staticText : ''
  const scroll = screen?.mode === 'text' ? screen.scrollText.slice(-240) : ''

  // 1) Loading and BASIC
  if ((sig?.fps ?? 0) > 62) { state = 'loading'; reasons.push(`running at ${sig?.fps} fps (warp while loading)`) }
  else if (screen?.basicBanner && /^(LOADING|SEARCHING FOR|LOAD"|RUN$)/.test(screen.lastLine)) {
    state = 'loading'; reasons.push(`BASIC screen ends with "${screen.lastLine}"`)
  } else if (screen?.basicBanner && screen.lastLine === 'READY.') {
    state = 'basic'
    reasons.push('BASIC READY. prompt on screen')
    if (/LOADING/.test(screen.staticText) && ctx.basicSince > 2500) {
      action = { key: 'RUN', confidence: 90, reason: 'the program loaded but did not start (READY. after LOADING)', source: 'basic', auto: true }
    }
  } else if (ctx.gameplay) {
    // A clear "press … to start" or a Y/N option screen means the game is not running yet, whatever
    // the motion and input signals say.
    const waiting = text && (findPrompt(text)?.strong || YN_RE.test(text))
    if (waiting) reasons.push('gameplay signals, but the screen is asking for a key')
    else { state = 'gameplay'; reasons.push('gameplay detected (joystick moved + motion)') }
  }

  // 2) Screen text: prompts, trainers, menus
  if (state === 'unknown' && screen) {
    const p = findPrompt(text, ctx.title)
    if (text && (YN_RE.test(text) || TRAINER_RE.test(text))) {
      state = 'trainer'
      reasons.push('trainer / option screen (Y/N or trainer words on screen)')
      hints.push('Y / N = choose options', 'Enter = RETURN')
    } else if (text && MENU_RE.test(text)) {
      state = 'menu'
      reasons.push('numbered menu on screen')
      hints.push('number keys = pick an option')
    } else if (text && QUESTION_RE.test(text)) {
      state = 'menu'
      reasons.push('a question on screen (Y/N answer)')
      hints.push('Y / N = answer', 'type your answer + Enter')
    }
    if (p) {
      prompt = p.line
      if (state === 'unknown') state = 'title'
      action = { key: p.key, confidence: p.strong ? 92 : 85, reason: `screen says "${p.line}"`, source: 'screen', auto: p.strong }
      reasons.push(`prompt on screen: "${p.line}" → ${p.key}`)
    } else if (scroll) {
      const sp = findPrompt(scroll)
      if (sp) {
        prompt = sp.line
        action = { key: sp.key, confidence: 78, reason: `scrolling text says "${sp.line}"`, source: 'scroller', auto: false }
        reasons.push(`prompt in scroller: "${sp.line}" → ${sp.key}`)
      }
    }
    if (state === 'unknown') {
      if (screen.mode === 'bitmap') { state = 'title'; reasons.push('picture on screen (bitmap mode) — title screen?') }
      else if (screen.scrollText) { state = 'intro'; reasons.push('scrolling text (intro / cracktro)') }
    }
  }

  // 3) What worked before (learned / built-in / guide) — beats an unsure screen reading.
  if (profile && !['loading', 'basic', 'gameplay'].includes(state) && screen) {
    const steps = profile.startupSequence
    const src = profile.startupSource ?? 'guide'
    const matched = steps.findIndex((st, i) => i >= 0 && matches(st.when, screen, signature))
    if (matched >= 0) {
      const st = steps[matched]
      const conf = src === 'learned' ? 95 : src === 'builtin' ? 93 : 70
      if (!action || conf > action.confidence) {
        action = { key: st.key, confidence: conf, reason: `${src === 'learned' ? 'worked here before' : `${src} profile`} ("${st.when}")`, source: src, auto: conf >= 90 }
      }
      reasons.push(`profile step ${matched + 1} matches this screen → ${st.key}`)
    } else if (!action && ctx.nextStep < steps.length && !steps[ctx.nextStep].when) {
      const st = steps[ctx.nextStep]
      action = { key: st.key, confidence: src === 'guide' ? 60 : 75, reason: `${src}: step ${ctx.nextStep + 1} of ${steps.length}`, source: src, auto: false }
      reasons.push(`${src} step ${ctx.nextStep + 1}: ${st.key}`)
    }
  }

  // 4) Joystick port
  let port: PortGuess | null = null
  const consider = (g: PortGuess) => { if (!port || g.confidence > port.confidence) port = g }
  const sp = text ? screenPort(text) : null
  if (sp) consider({ port: sp, confidence: 90, reason: 'the screen names the joystick port' })
  if (profile?.joystickPort === 1 || profile?.joystickPort === 2) {
    const conf = { learned: 90, builtin: 85, guide: 70, library: 55 }[profile.joystickPortSource ?? 'library'] ?? 55
    consider({ port: profile.joystickPort, confidence: conf, reason: `${profile.joystickPortSource ?? 'library'} profile` })
  }
  if (screen && (state === 'gameplay' || ctx.elapsed > 25)) {
    const { dc00Reads: r2, dc01Reads: r1 } = screen.ports
    // The program only ever reads one of the two joystick registers: that is the port it can use.
    if (r2 > 0 && r1 === 0) consider({ port: 2, confidence: 75, reason: 'the program reads joystick port 2 only ($DC00)' })
    else if (r1 > 0 && r2 === 0) consider({ port: 1, confidence: 75, reason: 'the program reads joystick port 1 only ($DC01)' })
  }

  // 5) Keys for Auto before the game runs: arrows are the joystick (menus, titles), Space is whatever this
  // screen needs — the space bar when the screen talks about SPACE, fire when it asks for fire, and when
  // nothing says which: the space bar followed by a short fire press.
  let keymap: Keymap = { arrows: 'joy', space: 'both' }
  // Screens of text read by the C64's own keyboard routine (text adventures, questions): typing.
  const typingScreen = !!screen && screen.mode === 'text' && screen.kernalIrq
    && (state === 'menu' || text.split(' / ').filter(wordy).length >= 4) && !action
  if (state === 'loading' || state === 'basic' || typingScreen) keymap = { arrows: 'cursor', space: 'space' }
  else if (state === 'gameplay') keymap = { arrows: 'joy', space: 'fire' }
  else if (state === 'trainer' || state === 'menu') keymap = { arrows: 'joy', space: 'space' }
  else if (/SPACE/.test(prompt ?? '') || action?.key === 'SPACE' || action?.key === 'ANY') keymap = { arrows: 'joy', space: 'space' }
  else if (action?.key === 'FIRE') keymap = { arrows: 'joy', space: 'fire' }

  if (action && ['RUN/STOP', 'RETURN', 'F1', 'F3', 'F5', 'F7'].includes(action.key) && /SPACE/.test(prompt ?? '')) {
    hints.push('Space = ' + (/SPACE\s+TO\s+([A-Z]+)/.exec(prompt ?? '')?.[1]?.toLowerCase() ?? 'space bar'))
  }
  return { state, signature, prompt, action, hints, port, keymap, reasons }
}

/** Remembers which key moved the game from one screen to the next (for "learn from successful inputs"). */
export class StartLearner {
  steps: { key: string; when: string }[] = []
  private lastSignature = ''
  private lastInputT = 0
  private pending: { key: string; t: number; when: string } | null = null

  /** Keys worth learning: startup keys, not joystick moves or letters typed into names. */
  static learnable(k: string): boolean {
    return ['FIRE', 'SPACE', 'FIRE+SPACE', 'RUN/STOP', 'RETURN', 'F1', 'F3', 'F5', 'F7', 'Y', 'N', 'ANY', 'RUN'].includes(k)
      || /^[0-9]$/.test(k)
  }

  update(a: Analysis, inputs: { k: string; t: number }[], now = Date.now()): { key: string; when: string } | null {
    if (a.state === 'gameplay' || a.state === 'loading') { this.pending = null; this.lastSignature = a.signature; return null }
    for (const i of inputs) {
      if (i.t <= this.lastInputT) continue
      this.lastInputT = i.t
      if (StartLearner.learnable(i.k) && this.lastSignature && this.lastSignature !== '(blank)') {
        this.pending = { key: i.k, t: i.t, when: this.lastSignature }
      }
    }
    let learned: { key: string; when: string } | null = null
    if (a.signature && a.signature !== this.lastSignature) {
      if (this.pending && now - this.pending.t < 4000 && this.pending.when !== a.signature) {
        learned = { key: this.pending.key, when: this.pending.when }
        if (!this.steps.some((s) => s.when === learned!.when)) this.steps.push(learned)
        if (this.steps.length > 8) this.steps.shift()
      }
      this.pending = null
      this.lastSignature = a.signature
    }
    return learned
  }

  reset() { this.steps = []; this.lastSignature = ''; this.pending = null; this.lastInputT = 0 }
}
