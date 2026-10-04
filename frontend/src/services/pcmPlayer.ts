/** Plays s16le stereo PCM chunks from /ws/audio with WebAudio. */
export class PcmPlayer {
  private ctx: AudioContext | null = null
  private ws: WebSocket | null = null
  private next = 0
  private rate = 47983
  private pending: Int16Array[] = []

  start() {
    this.ctx = new AudioContext()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws/audio`)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        this.rate = JSON.parse(ev.data).sampleRate ?? this.rate
        return
      }
      this.pending.push(new Int16Array(ev.data))
      if (this.pending.length >= 8) this.flush()
    }
  }

  private flush() {
    const ctx = this.ctx
    if (!ctx) return
    const total = this.pending.reduce((n, a) => n + a.length, 0) / 2
    const buf = ctx.createBuffer(2, total, this.rate)
    const l = buf.getChannelData(0)
    const r = buf.getChannelData(1)
    let i = 0
    for (const chunk of this.pending) {
      for (let j = 0; j < chunk.length; j += 2) {
        l[i] = chunk[j] / 32768
        r[i] = chunk[j + 1] / 32768
        i++
      }
    }
    this.pending = []
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    const now = ctx.currentTime
    if (this.next < now + 0.02 || this.next > now + 0.5) this.next = now + 0.08 // resync on under/overrun
    src.start(this.next)
    this.next += buf.duration
  }

  /** Browsers may block sound until the user interacts with the page. */
  get blocked(): boolean {
    return this.ctx?.state === 'suspended'
  }

  async resume(): Promise<boolean> {
    try {
      await this.ctx?.resume()
    } catch {
      /* ignored */
    }
    return !this.blocked
  }

  stop() {
    this.ws?.close()
    this.ctx?.close()
    this.ws = null
    this.ctx = null
  }
}

// The C64's own speakers / TV usually play the sound already: this page is muted until you unmute it
// (a new key, so an old "sound on" from before doesn't carry over; your choice is remembered from now on).
const SOUND_PREF = 'c64.display.sound.v2'

export function soundPreferred(): boolean {
  try {
    return localStorage.getItem(SOUND_PREF) === 'on'
  } catch {
    return false
  }
}

export function setSoundPreferred(on: boolean) {
  try {
    localStorage.setItem(SOUND_PREF, on ? 'on' : 'off')
  } catch {
    /* ignored */
  }
}
