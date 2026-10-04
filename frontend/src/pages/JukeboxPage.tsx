import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Card, Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { errorMessage } from '../services/api'
import { jukeboxApi } from '../services/jukeboxApi'
import type { HistoryPlay, NowPlaying, Station, StationSummary, Tune } from '../services/jukeboxApi'
import './jukebox.css'

type SearchBy = 'title' | 'composer'
const DURATIONS = [60, 120, 180, 240, 300, 600]
const DURATION_KEY = 'c64.jukebox.trackSeconds'

function loadDuration(): number {
  try {
    const v = Number(window.localStorage.getItem(DURATION_KEY))
    return DURATIONS.includes(v) ? v : 180
  } catch {
    return 180
  }
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, '0')}`

/** A station being played: which track, and when the next one starts. */
interface Queue { station: Station; index: number; deadline: number }

export function JukeboxPage() {
  const toast = useToast()
  const { status } = useLive()
  const connected = !!status?.connected
  const [sidOk, setSidOk] = useState(true)
  const [catalogOk, setCatalogOk] = useState(true)

  const [query, setQuery] = useState('')
  const [by, setBy] = useState<SearchBy>('title')
  const [tunes, setTunes] = useState<Tune[] | null>(null)
  const [searching, setSearching] = useState(false)

  const [now, setNow] = useState<NowPlaying | null>(null)
  const [busy, setBusy] = useState<string | null>(null)   // key of the tune being started, or 'stop'

  const [stations, setStations] = useState<StationSummary[]>([])
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [prompt, setPrompt] = useState('')
  const [making, setMaking] = useState(false)
  const [open, setOpen] = useState<Station | null>(null)

  const [history, setHistory] = useState<HistoryPlay[]>([])
  const [queue, setQueue] = useState<Queue | null>(null)
  const [trackSeconds, setTrackSeconds] = useState(loadDuration)
  const [tick, setTick] = useState(() => Date.now())

  const canPlay = connected && sidOk

  const loadHistory = useCallback(() => {
    jukeboxApi.history().then((r) => setHistory(r.plays)).catch(() => {})
  }, [])
  const loadStations = useCallback(() => {
    jukeboxApi.stations().then((r) => { setStations(r.stations); setSuggestions(r.suggestions) })
      .catch((e) => toast(errorMessage(e), 'error'))
  }, [toast])

  useEffect(() => {
    jukeboxApi.status().then((s) => { setSidOk(s.sidPlayback || !s.connected); setCatalogOk(s.catalog); setNow(s.nowPlaying) })
      .catch(() => {})
    loadStations()
    loadHistory()
  }, [connected, loadStations, loadHistory])

  const play = useCallback(async (t: { id: string; category: number; title?: string; composer?: string | null },
    opts: { song?: number; stationId?: number } = {}) => {
    const key = `${t.category}-${t.id}`
    setBusy(key)
    try {
      const r = await jukeboxApi.play({ id: t.id, category: t.category, title: t.title, composer: t.composer,
        song: opts.song, stationId: opts.stationId })
      setNow(r.track)
      loadHistory()
      return true
    } catch (e) {
      toast(errorMessage(e), 'error')
      return false
    } finally {
      setBusy(null)
    }
  }, [toast, loadHistory])

  // ------------------------------------------------------------ station playback (driven from here)
  const playQueueAt = useCallback(async (station: Station, index: number) => {
    // Skip tracks that fail (e.g. a missing file), but don't loop forever.
    for (let i = index, tries = 0; i < station.tracks.length && tries < 3; i++, tries++) {
      const t = station.tracks[i]
      if (await play(t, { stationId: station.id })) {
        setQueue({ station, index: i, deadline: Date.now() + trackSeconds * 1000 })
        return
      }
    }
    setQueue(null)
  }, [play, trackSeconds])

  const next = useCallback(() => {
    if (!queue) return
    if (queue.index + 1 >= queue.station.tracks.length) {
      setQueue(null)
      toast(`📻 End of “${queue.station.name}”`, 'info')
      return
    }
    void playQueueAt(queue.station, queue.index + 1)
  }, [queue, playQueueAt, toast])

  // The auto-advance timer: one tick a second while a station plays.
  const nextRef = useRef(next)
  useEffect(() => { nextRef.current = next }, [next])
  useEffect(() => {
    if (!queue) return
    const h = window.setInterval(() => {
      setTick(Date.now())
      if (Date.now() >= queue.deadline && !busy) nextRef.current()
    }, 1000)
    return () => window.clearInterval(h)
  }, [queue, busy])

  const setDuration = (s: number) => {
    setTrackSeconds(s)
    try { window.localStorage.setItem(DURATION_KEY, String(s)) } catch { /* per-browser convenience only */ }
    setQueue((q) => q && { ...q, deadline: q.deadline + (s - trackSeconds) * 1000 })
  }

  const stop = async () => {
    setBusy('stop')
    try {
      await jukeboxApi.stop()
      setNow(null)
      setQueue(null)
      toast('⏹ Music stopped (the C64 was reset)', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(null)
    }
  }

  const changeSong = (song: number) => {
    if (!now) return
    void play(now, { song, stationId: now.stationId ?? undefined })
  }

  // ------------------------------------------------------------ search / stations
  const search = async (e?: FormEvent) => {
    e?.preventDefault()
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      const r = await jukeboxApi.search(by === 'title' ? q : '', by === 'composer' ? q : '')
      setTunes(r.tunes)
    } catch (err) {
      toast(errorMessage(err), 'error')
    } finally {
      setSearching(false)
    }
  }

  const makeStation = async (text?: string) => {
    const p = (text ?? prompt).trim()
    if (p.length < 2) return
    setPrompt(p)
    setMaking(true)
    try {
      const st = await jukeboxApi.makeStation(p)
      setOpen(st)
      loadStations()
      toast(`🤖 “${st.name}”: ${st.tracks.length} tunes${st.dropped ? ` (${st.dropped} not found in HVSC)` : ''}`, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setMaking(false)
    }
  }

  const openStation = async (id: number) => {
    if (open?.id === id) { setOpen(null); return }
    try { setOpen(await jukeboxApi.station(id)) } catch (e) { toast(errorMessage(e), 'error') }
  }

  const deleteStation = async (id: number) => {
    if (!window.confirm('Delete this station?')) return
    try {
      await jukeboxApi.deleteStation(id)
      if (open?.id === id) setOpen(null)
      if (queue?.station.id === id) setQueue(null)
      loadStations()
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const remaining = queue ? Math.max(0, Math.round((queue.deadline - tick) / 1000)) : 0
  const playLabel = (t: { id: string; category: number }) => busy === `${t.category}-${t.id}` ? <Spinner /> : '▶ Play on C64'

  return (
    <div className="page jukebox">
      <div className="page-head">
        <h1>🎵 SID Jukebox</h1>
        <span className={`chip ${canPlay ? 'on' : ''}`}>{canPlay ? '🔊 Real SID ready' : '🔇 C64 not ready'}</span>
      </div>

      {!connected && (
        <div className="jb-banner">🔌 The C64 Ultimate isn't connected — connect it to hear tunes on its real SID chip. You can still search and build stations.</div>
      )}
      {connected && !sidOk && (
        <div className="jb-banner">This Ultimate's firmware doesn't offer SID playback over its network API, so tunes can't be played from here.</div>
      )}
      {!catalogOk && (
        <div className="jb-banner">📚 The Assembly64 catalog isn't set up — add it under Settings → Online catalog to search HVSC.</div>
      )}

      {now && (
        <div className="jb-now" aria-live="polite">
          <div className="jb-now-info">
            <div className="jb-now-title">🎶 {now.title}</div>
            <div className="muted small">
              {[now.composer, now.released].filter(Boolean).join(' · ') || 'Unknown composer'}
              {queue && <> · 📻 {queue.station.name} ({queue.index + 1}/{queue.station.tracks.length})</>}
            </div>
            {queue && (
              <div className="jb-progress" title="Time until the next track">
                <div style={{ width: `${100 - (remaining / trackSeconds) * 100}%` }} />
              </div>
            )}
          </div>
          <div className="jb-now-ctrl">
            {now.songs > 1 && (
              <label className="small muted jb-sub">
                Sub-tune
                <select value={now.song} disabled={!canPlay || !!busy} onChange={(e) => changeSong(Number(e.target.value))}>
                  {Array.from({ length: now.songs }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>{n}{n === now.startSong ? ' ★' : ''}</option>
                  ))}
                </select>
              </label>
            )}
            {queue && <span className="small muted jb-timer">⏱ {mmss(remaining)}</span>}
            {queue && <button className="btn btn-sm" onClick={next} disabled={!canPlay || !!busy}>⏭ Next</button>}
            <button className="btn btn-sm" onClick={stop} disabled={!connected || !!busy} title="Stops the music by resetting the C64">
              {busy === 'stop' ? <Spinner /> : '⏹ Stop'}
            </button>
          </div>
        </div>
      )}

      <div className="jb-grid">
        <div className="jb-col">
          <Card title="🔎 Search HVSC">
            <form className="jb-search" onSubmit={search}>
              <div className="seg" role="group" aria-label="Search by">
                <button type="button" className={`seg-btn ${by === 'title' ? 'on' : ''}`} onClick={() => setBy('title')}>Title</button>
                <button type="button" className={`seg-btn ${by === 'composer' ? 'on' : ''}`} onClick={() => setBy('composer')}>Composer</button>
              </div>
              <input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={60}
                placeholder={by === 'title' ? 'e.g. Commando, Wizball, Last Ninja' : 'e.g. Rob Hubbard, Galway, Tel'} aria-label="Search" />
              <button className="btn btn-primary" type="submit" disabled={searching || !query.trim()}>{searching ? <Spinner /> : 'Search'}</button>
            </form>
            {tunes && tunes.length === 0 && <Empty>No tunes found. Try a shorter title or just the composer's surname.</Empty>}
            {tunes && tunes.length > 0 && (
              <ul className="jb-list">
                {tunes.map((t) => (
                  <li key={`${t.category}-${t.id}`}>
                    <div className="jb-track">
                      <div className="jb-track-title">{t.title}</div>
                      <div className="muted small">{[t.composer, t.year, t.source].filter(Boolean).join(' · ')}</div>
                    </div>
                    <button className="btn btn-sm" disabled={!canPlay || !!busy} onClick={() => { setQueue(null); void play(t) }}>
                      {playLabel(t)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="🕘 Recently played">
            {history.length === 0 ? <Empty>Nothing played yet.</Empty> : (
              <ul className="jb-list">
                {history.map((h, i) => (
                  <li key={`${h.playedAt}-${i}`}>
                    <div className="jb-track">
                      <div className="jb-track-title">{h.title}{h.songs && h.songs > 1 ? <span className="muted small"> · song {h.song}</span> : null}</div>
                      <div className="muted small">
                        {[h.composer, h.playedAt && new Date(h.playedAt).toLocaleString()].filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    <button className="btn btn-sm btn-ghost" disabled={!canPlay || !!busy}
                      onClick={() => { setQueue(null); void play(h, { song: h.song ?? undefined }) }}>▶</button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="jb-col">
          <Card title="🤖 Make a station">
            <form className="jb-search" onSubmit={(e) => { e.preventDefault(); void makeStation() }}>
              <input value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={200}
                placeholder="e.g. Rob Hubbard classics, chill demo tunes…" aria-label="Station idea" />
              <button className="btn btn-primary" type="submit" disabled={making || prompt.trim().length < 2}>
                {making ? <Spinner /> : 'Make it'}
              </button>
            </form>
            <div className="chips">
              {suggestions.map((s) => (
                <button key={s} className="chip" disabled={making} onClick={() => void makeStation(s)}>{s}</button>
              ))}
            </div>
            {making && <p className="muted small">Picking tunes and finding them in the HVSC catalog…</p>}
          </Card>

          <Card title="📻 Stations" actions={
            <label className="small muted jb-sub">
              Each track
              <select value={trackSeconds} onChange={(e) => setDuration(Number(e.target.value))}>
                {DURATIONS.map((d) => <option key={d} value={d}>{d / 60} min</option>)}
              </select>
            </label>
          }>
            {stations.length === 0 ? <Empty>No stations yet — make one above.</Empty> : (
              <ul className="jb-stations">
                {stations.map((s) => (
                  <li key={s.id} className={open?.id === s.id ? 'open' : ''}>
                    <div className="jb-station-head">
                      <button className="jb-station-name" onClick={() => void openStation(s.id)}>
                        <strong>{s.name}</strong>
                        <span className="muted small">{s.trackCount} tunes · “{s.prompt}”</span>
                      </button>
                      <button className="btn btn-sm btn-ghost" onClick={() => void deleteStation(s.id)} aria-label="Delete station">🗑</button>
                    </div>
                    {open?.id === s.id && (
                      <div className="jb-station-body">
                        {open.description && <p className="muted small">{open.description}</p>}
                        <button className="btn btn-primary btn-sm" disabled={!canPlay || !!busy || open.tracks.length === 0}
                          onClick={() => void playQueueAt(open, 0)}>▶ Play station</button>
                        <ol className="jb-list">
                          {open.tracks.map((t, i) => {
                            const current = queue?.station.id === open.id && queue.index === i
                            return (
                              <li key={`${t.category}-${t.id}`} className={current ? 'current' : ''}>
                                <div className="jb-track">
                                  <div className="jb-track-title">{current ? '🔊 ' : ''}{t.title}</div>
                                  <div className="muted small">{[t.composer, t.source].filter(Boolean).join(' · ')}</div>
                                </div>
                                <button className="btn btn-sm btn-ghost" disabled={!canPlay || !!busy}
                                  onClick={() => void playQueueAt(open, i)}>{busy === `${t.category}-${t.id}` ? <Spinner /> : '▶'}</button>
                              </li>
                            )
                          })}
                        </ol>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
      {!now && <p className="muted small">Tunes come from the High Voltage SID Collection via Assembly64 and play on your C64's own SID chip. ⏹ Stop resets the C64.</p>}
    </div>
  )
}
