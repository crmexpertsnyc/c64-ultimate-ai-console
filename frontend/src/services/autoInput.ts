/**
 * Auto input mode for Browser Play: decides between TYPE (whole keyboard = C64 keyboard) and PLAY
 * (arrows + Space = joystick; other keys still type) from the game's state and the connected controllers.
 *
 * Priority: user override → known game profile → automatic detection → safe default (TYPE).
 * Rules:
 *   - Every game boots in TYPE (intros, cracktros, trainers and menus need the keyboard).
 *   - AUTO switches to PLAY when the joystick is in use (a controller, or the arrow keys — which the startup
 *     analyzer makes the joystick on title screens) AND gameplay looks like it has started.
 *   - Before that, the startup analyzer (startAnalyzer.ts) picks a mixed key map per screen.
 *   - Controller disconnected while in PLAY → back to TYPE.
 * Profiles are stored in this browser for now (one small JSON per game), shaped so they can later move
 * to the console's database and be shared.
 */

export type InputPref = 'auto' | 'type' | 'play'
export type InputMode = 'type' | 'play'

export interface InputSignals {
  started: boolean
  pads: { id: string; index: number; player?: number }[]  // player: 1 = port 2, 2 = port 1, 0 = none yet
  lastPadAt: number        // ms epoch of the last controller button / stick input (0 = none)
  lastKeyAt: number        // ms epoch of the last key press in the player
  lastJoyKeyAt?: number    // ms epoch of the last arrow / Space used as the joystick (keyboard players)
  lastJoyDirAt?: number    // ms epoch the joystick was last moved (controller stick / d-pad, or arrows as joystick)
  startedAt: number        // ms epoch when the emulator started running the game
  motion: number[]         // fraction of the screen that changed between recent samples (1.5 s apart)
  lastBigChangeAt: number  // ms epoch of the last big scene change (> 35% of the screen)
  fps: number
}

export interface GameProfile {
  startupMode: 'TYPE'
  gameplayMode: 'PLAY'
  gameplayAfter?: number   // seconds after boot when gameplay started last time
  joystickPort?: 1 | 2     // port the user chose for this game
  updatedAt: number
}

export interface Signal { key: string; label: string; points: number; on: boolean }

export const THRESHOLD = 60

const profileKey = (gameId: number) => `c64.inputProfile.${gameId}`

export function loadProfile(gameId: number): GameProfile | null {
  try {
    const raw = localStorage.getItem(profileKey(gameId))
    return raw ? (JSON.parse(raw) as GameProfile) : null
  } catch {
    return null
  }
}

export function saveProfile(gameId: number, patch: Partial<GameProfile>): GameProfile {
  const next: GameProfile = { startupMode: 'TYPE', gameplayMode: 'PLAY', ...(loadProfile(gameId) ?? {}), ...patch, updatedAt: Date.now() }
  try { localStorage.setItem(profileKey(gameId), JSON.stringify(next)) } catch { /* storage unavailable */ }
  return next
}

/** Remembers inputs so "a big scene change shortly after the player pressed something" can be seen. */
export class TransitionTracker {
  private inputs: number[] = []
  seen = false

  update(sig: InputSignals) {
    for (const t of [sig.lastKeyAt, sig.lastPadAt]) {
      if (t && !this.inputs.includes(t)) this.inputs.push(t)
    }
    if (this.inputs.length > 30) this.inputs.splice(0, this.inputs.length - 30)
    const change = sig.lastBigChangeAt
    if (change && sig.startedAt && change > sig.startedAt + 4000
      && this.inputs.some((t) => change - t >= 0 && change - t <= 8000)) this.seen = true
  }

  reset() { this.inputs = []; this.seen = false }
}

/** Gameplay confidence (0–100) from the available signals, with the reasons. */
export function gameplayConfidence(sig: InputSignals, transitionSeen: boolean, profile: GameProfile | null,
  now = Date.now()): { score: number; signals: Signal[]; likely: boolean } {
  const elapsed = sig.startedAt ? (now - sig.startedAt) / 1000 : 0
  const recent = sig.motion.slice(-4)
  const signals: Signal[] = [
    // Moving the joystick (not just pressing fire, which is how title screens are skipped).
    { key: 'pad', label: 'Joystick moved after boot (controller or arrow keys)', points: 30,
      on: !!sig.startedAt && (sig.lastJoyDirAt ?? sig.lastPadAt) > sig.startedAt + 3000 },
    { key: 'motion', label: 'Continuous screen motion', points: 25,
      on: recent.length >= 4 && recent.filter((m) => m > 0.02).length >= 3 },
    { key: 'transition', label: 'Scene changed right after your input', points: 20, on: transitionSeen },
    { key: 'loaded', label: 'Loading likely finished (15 s+)', points: 10, on: elapsed > 15 },
    { key: 'profile', label: 'This game reached gameplay at this point before', points: 15,
      on: !!profile?.gameplayAfter && elapsed >= profile.gameplayAfter * 0.8 },
  ]
  const score = Math.min(100, signals.filter((s) => s.on).reduce((n, s) => n + s.points, 0))
  // A scrolling intro plus an impatient button press is not enough on its own: also require evidence
  // of progress (a scene change after input, or this game's history) unless the score is very high.
  const progress = signals.some((s) => (s.key === 'transition' || s.key === 'profile') && s.on)
  return { score, signals, likely: score >= THRESHOLD && (progress || score >= 80) }
}

/** The mode to use now. `current` gives hysteresis: once AUTO reaches PLAY it stays there. */
export function decideMode(pref: InputPref, joystickInUse: boolean, likely: boolean, current: InputMode): InputMode {
  if (pref === 'type' || pref === 'play') return pref
  if (!joystickInUse) return 'type'
  return current === 'play' || likely ? 'play' : 'type'
}
