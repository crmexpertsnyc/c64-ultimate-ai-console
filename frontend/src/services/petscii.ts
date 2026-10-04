/**
 * 📟 PETSCII terminal: a 40×25 Commodore 64 screen driven by the bytes a PETSCII BBS sends.
 *
 * Handled: printable characters in both character sets (upper case/graphics and lower/upper case), the 16 colour
 * codes, reverse on/off, cursor up/down/left/right, home, clear, insert/delete, return, character-set switching.
 * Not handled (documented in docs/bbs.md): blinking, sprites, sound, the C64's screen-editor line linking, and
 * colour/attribute tricks that poke screen memory directly. Text is drawn on a canvas with the public-domain
 * Unscii-8 font — remote content is never turned into HTML.
 */

const NBSP = String.fromCharCode(0xa0)
export const COLS = 40
export const ROWS = 25

// Pepto's C64 palette
export const PALETTE = ['#000000', '#ffffff', '#68372b', '#70a4b2', '#6f3d86', '#588d43', '#352879', '#b8c76f',
  '#6f4f25', '#433900', '#9a6759', '#444444', '#6c6c6c', '#9ad284', '#6c5eb5', '#959595']

const COLOR_CODES: Record<number, number> = {
  0x90: 0, 0x05: 1, 0x1c: 2, 0x9f: 3, 0x9c: 4, 0x1e: 5, 0x1f: 6, 0x9e: 7,
  0x81: 8, 0x95: 9, 0x96: 10, 0x97: 11, 0x98: 12, 0x99: 13, 0x9a: 14, 0x9b: 15,
}

/** Screen codes 0x40–0x7F of the upper case / graphics set (Unicode "Symbols for Legacy Computing" mapping). */
const GFX = [
  0x2500, 0x2660, 0x1fb72, 0x1fb78, 0x1fb77, 0x1fb76, 0x1fb7a, 0x1fb71, 0x1fb74, 0x256e, 0x2570, 0x256f, 0x1fb7c, 0x2572, 0x2571, 0x1fb7d,
  0x1fb7e, 0x25cf, 0x1fb7b, 0x2665, 0x1fb70, 0x256d, 0x2573, 0x25cb, 0x2663, 0x1fb75, 0x2666, 0x253c, 0x1fb8c, 0x2502, 0x3c0, 0x25e5,
  0xa0, 0x258c, 0x2584, 0x2594, 0x2581, 0x258f, 0x2592, 0x2595, 0x1fb8f, 0x25e4, 0x1fb87, 0x251c, 0x2597, 0x2514, 0x2510, 0x2582,
  0x250c, 0x2534, 0x252c, 0x2524, 0x258e, 0x258d, 0x1fb88, 0x1fb82, 0x1fb83, 0x2583, 0x1fb7f, 0x2596, 0x259d, 0x2518, 0x2598, 0x259a,
]

function buildSet(lower: boolean): string[] {
  const out: string[] = new Array(128)
  for (let c = 0; c < 128; c++) {
    let cp: number
    if (c === 0) cp = 0x40
    else if (c <= 0x1a) cp = (lower ? 0x60 : 0x40) + c            // a–z (lower set) or A–Z
    else if (c === 0x1b) cp = 0x5b
    else if (c === 0x1c) cp = 0xa3                                  // £
    else if (c === 0x1d) cp = 0x5d
    else if (c === 0x1e) cp = 0x2191                                // ↑
    else if (c === 0x1f) cp = 0x2190                                // ←
    else if (c < 0x40) cp = c                                       // space, digits, punctuation
    else if (lower && c >= 0x41 && c <= 0x5a) cp = c                // A–Z in the lower case set
    else cp = GFX[c - 0x40]
    if (lower) {
      if (c === 0x5e) cp = 0x1fb95
      else if (c === 0x5f) cp = 0x1fb98
      else if (c === 0x69) cp = 0x1fb99
      else if (c === 0x7a) cp = 0x2713
    }
    out[c] = String.fromCodePoint(cp)
  }
  return out
}
export const UPPER_SET = buildSet(false)
export const LOWER_SET = buildSet(true)

/** A printable PETSCII byte → screen code (0–127), or -1 for a control code. */
export function petsciiToScreen(b: number): number {
  if (b >= 0x20 && b <= 0x3f) return b
  if (b >= 0x40 && b <= 0x5f) return b - 0x40
  if (b >= 0x60 && b <= 0x7f) return b - 0x20
  if (b >= 0xa0 && b <= 0xbf) return b - 0x40
  if (b >= 0xc0 && b <= 0xdf) return b - 0x80
  if (b >= 0xe0 && b <= 0xfe) return b - 0x80
  if (b === 0xff) return 0x5e
  return -1
}

export class PetsciiScreen {
  chars = new Uint8Array(COLS * ROWS).fill(0x20)   // screen codes, +0x80 = reverse
  colors = new Uint8Array(COLS * ROWS).fill(14)
  x = 0
  y = 0
  fg = 14
  bg = 6
  border = 14
  rvs = false
  lower = true                                      // most PETSCII boards switch to lower case; they send 0x0E/0x8E
  dirty = true

  reset(): void {
    this.chars.fill(0x20); this.colors.fill(14)
    this.x = 0; this.y = 0; this.fg = 14; this.rvs = false; this.dirty = true
  }

  private scroll(): void {
    this.chars.copyWithin(0, COLS); this.colors.copyWithin(0, COLS)
    this.chars.fill(0x20, COLS * (ROWS - 1)); this.colors.fill(this.fg, COLS * (ROWS - 1))
  }

  private newline(): void {
    this.x = 0
    if (++this.y >= ROWS) { this.y = ROWS - 1; this.scroll() }
  }

  write(data: Uint8Array): void {
    for (const b of data) this.put(b)
    this.dirty = true
  }

  put(b: number): void {
    const color = COLOR_CODES[b]
    if (color !== undefined) { this.fg = color; return }
    switch (b) {
      case 0x0d: case 0x8d: this.rvs = false; this.newline(); return
      case 0x12: this.rvs = true; return
      case 0x92: this.rvs = false; return
      case 0x11: if (++this.y >= ROWS) { this.y = ROWS - 1; this.scroll() } return
      case 0x91: if (this.y > 0) this.y--; return
      case 0x1d: if (++this.x >= COLS) this.newline(); return
      case 0x9d: if (this.x > 0) this.x--; else if (this.y > 0) { this.y--; this.x = COLS - 1 } return
      case 0x13: this.x = 0; this.y = 0; return
      case 0x93: this.chars.fill(0x20); this.colors.fill(this.fg); this.x = 0; this.y = 0; return
      case 0x0e: this.lower = true; return
      case 0x8e: this.lower = false; return
      case 0x14: {                                  // DEL: back one, pull the rest of the line left
        if (this.x === 0 && this.y === 0) return
        if (this.x > 0) this.x--; else { this.y--; this.x = COLS - 1 }
        const row = this.y * COLS
        this.chars.copyWithin(row + this.x, row + this.x + 1, row + COLS)
        this.colors.copyWithin(row + this.x, row + this.x + 1, row + COLS)
        this.chars[row + COLS - 1] = 0x20
        return
      }
      case 0x94: {                                  // INST: push the rest of the line right
        const row = this.y * COLS
        this.chars.copyWithin(row + this.x + 1, row + this.x, row + COLS - 1)
        this.colors.copyWithin(row + this.x + 1, row + this.x, row + COLS - 1)
        this.chars[row + this.x] = 0x20
        return
      }
    }
    const sc = petsciiToScreen(b)
    if (sc < 0) return                              // other control codes (bell, lock/unlock case…) ignored
    const i = this.y * COLS + this.x
    this.chars[i] = sc | (this.rvs ? 0x80 : 0)
    this.colors[i] = this.fg
    if (++this.x >= COLS) this.newline()
  }

  /** The screen as text (tests and copy-out). */
  text(): string {
    const set = this.lower ? LOWER_SET : UPPER_SET
    const lines: string[] = []
    for (let r = 0; r < ROWS; r++) {
      let s = ''
      for (let c = 0; c < COLS; c++) s += set[this.chars[r * COLS + c] & 0x7f]
      lines.push(s.replace(/\s+$/, ''))
    }
    return lines.join('\n').replace(/\n+$/, '')
  }

  draw(ctx: CanvasRenderingContext2D, scale: number, cursorOn: boolean): void {
    const cw = 8 * scale
    const set = this.lower ? LOWER_SET : UPPER_SET
    ctx.textBaseline = 'top'
    ctx.font = `${8 * scale}px unscii8, monospace`
    ctx.fillStyle = PALETTE[this.bg]
    ctx.fillRect(0, 0, COLS * cw, ROWS * cw)
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const i = r * COLS + c
        const code = this.chars[i]
        const reverse = (code & 0x80) !== 0
        const fg = PALETTE[this.colors[i]]
        if (reverse) { ctx.fillStyle = fg; ctx.fillRect(c * cw, r * cw, cw, cw) }
        const ch = set[code & 0x7f]
        if (ch !== ' ' && ch !== NBSP) {
          ctx.fillStyle = reverse ? PALETTE[this.bg] : fg
          ctx.fillText(ch, c * cw, r * cw)
        }
      }
    }
    if (cursorOn) {
      ctx.fillStyle = PALETTE[this.fg]
      ctx.globalAlpha = 0.85
      ctx.fillRect(this.x * cw, this.y * cw, cw, cw)
      ctx.globalAlpha = 1
    }
  }
}

// ---------------------------------------------------------------- keyboard → PETSCII
export const PETSCII_KEYS = {
  RETURN: 0x0d, DEL: 0x14, INST: 0x94, HOME: 0x13, CLR: 0x93, UP: 0x91, DOWN: 0x11, LEFT: 0x9d, RIGHT: 0x1d,
  STOP: 0x03, F1: 0x85, F3: 0x86, F5: 0x87, F7: 0x88, F2: 0x89, F4: 0x8a, F6: 0x8b, F8: 0x8c,
  LEFT_ARROW: 0x5f, UP_ARROW: 0x5e, POUND: 0x5c, RVS_ON: 0x12, RVS_OFF: 0x92,
} as const

const CTRL_COLORS = [0x92, 0x90, 0x05, 0x1c, 0x9f, 0x9c, 0x1e, 0x1f, 0x9e, 0x12]   // Ctrl+0…9 (0 = RVS off, 9 = RVS on)

/** A browser keyboard event → PETSCII bytes (null = not a key the C64 has). */
export function keyToPetscii(e: { key: string; shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean }): number[] | null {
  const k = e.key
  if (e.metaKey || e.altKey) return null
  if (e.ctrlKey) {
    if (/^[0-9]$/.test(k)) return [CTRL_COLORS[Number(k)]]
    return null
  }
  switch (k) {
    case 'Enter': return [0x0d]
    case 'Backspace': case 'Delete': return [0x14]
    case 'Insert': return [0x94]
    case 'Home': return [e.shiftKey ? 0x93 : 0x13]
    case 'ArrowUp': return [0x91]
    case 'ArrowDown': return [0x11]
    case 'ArrowLeft': return [0x9d]
    case 'ArrowRight': return [0x1d]
    case 'Escape': return [0x03]
    case 'F1': return [0x85]
    case 'F2': return [0x89]
    case 'F3': return [0x86]
    case 'F4': return [0x8a]
    case 'F5': return [0x87]
    case 'F6': return [0x8b]
    case 'F7': return [0x88]
    case 'F8': return [0x8c]
    case '£': return [0x5c]
    case '^': return [0x5e]
    case '_': return [0x5f]
  }
  return [...k].length === 1 ? textToPetscii(k) : null   // other named keys (Tab, PageUp…) the C64 lacks
}

/** Typed / pasted text → PETSCII (lower case letters → 0x41–0x5A, capitals → 0xC1–0xDA). */
export function textToPetscii(s: string): number[] | null {
  if (!s) return null
  const out: number[] = []
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0
    if (ch.length > 1 || c > 0x7e) {
      if (ch === '£') out.push(0x5c)
      continue
    }
    if (c >= 0x61 && c <= 0x7a) out.push(c - 0x20)            // a–z
    else if (c >= 0x41 && c <= 0x5a) out.push(c + 0x80)       // A–Z
    else if (c === 0x0a || c === 0x0d) out.push(0x0d)
    else if (c >= 0x20 && c <= 0x5d) out.push(c)
    else if (c === 0x5f) out.push(0x5f)
    else if (c === 0x5e) out.push(0x5e)
  }
  return out.length ? out : null
}

// ---------------------------------------------------------------- ANSI: CP437 → Unicode
const CP437_HIGH = 'ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■' + NBSP
const CP437_REVERSE = new Map<string, number>([...CP437_HIGH].map((ch, i) => [ch, 0x80 + i]))

export function decodeCp437(data: Uint8Array): string {
  let s = ''
  for (const b of data) s += b < 0x80 ? String.fromCharCode(b) : CP437_HIGH[b - 0x80]
  return s
}

/** What the ANSI terminal types → bytes (Backspace as 0x08; characters outside CP437 become "?"). */
export function encodeCp437(s: string): Uint8Array {
  const out: number[] = []
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0x3f
    if (c === 0x7f) out.push(0x08)
    else if (c < 0x80) out.push(c)
    else out.push(CP437_REVERSE.get(ch) ?? 0x3f)
  }
  return new Uint8Array(out)
}
