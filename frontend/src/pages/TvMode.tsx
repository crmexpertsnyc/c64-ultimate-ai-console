import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CoverArt, titleArt } from '../components/CoverArt'
import { WhoIsPlaying } from '../components/ProfileSwitcher'
import type { Profile } from '../components/ProfileSwitcher'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'
import { currentProfile, setCurrentProfile } from '../services/profile'
import type { CatalogItem, Game } from '../shared/types'

/** One tile on a rail. */
interface Tile {
  key: string
  title: string
  cover?: string | null
  subtitle?: string
  gameId?: number | null
  catalog?: CatalogItem | null
  action?: () => void          // quick actions
  resume?: boolean
  playlistId?: number
}
interface Rail { title: string; tiles: Tile[] }

const REPEAT_MS = 180

/**
 * 📺 TV / couch mode: a full-screen, 10-foot home screen for the TV, driven by a gamepad, a TV remote or the
 * keyboard. D-pad / arrows move · A / Enter select · B / Esc back. Games play on the real C64 (on the TV) or
 * right here in the browser, full screen.
 */
export function TvMode() {
  const navigate = useNavigate()
  const toast = useToast()
  const [who, setWho] = useState<{ profiles: Profile[]; emojis: string[] } | null>(null)
  const [asking, setAsking] = useState(() => { try { return !sessionStorage.getItem('c64.tv.who') } catch { return true } })
  const [rails, setRails] = useState<Rail[]>([])
  const [pos, setPos] = useState({ row: 0, col: 0 })
  const [open, setOpen] = useState<Tile | null>(null)
  const [openIx, setOpenIx] = useState(0)
  const [whoIx, setWhoIx] = useState(0)
  const [clock, setClock] = useState(() => new Date())
  const railRefs = useRef<(HTMLElement | null)[]>([])

  useEffect(() => { api.profiles().then((r) => { setWho(r); if (r.profiles.length < 2) setAsking(false) }).catch(() => setAsking(false)) }, [])
  useEffect(() => { const t = window.setInterval(() => setClock(new Date()), 30000); return () => window.clearInterval(t) }, [])

  const me = who?.profiles.find((p) => String(p.id) === currentProfile()) ?? who?.profiles[0]

  const load = useCallback(async () => {
    const [saves, recs, lists, favs, recent, lib] = await Promise.all([
      api.saves().catch(() => ({ saves: [] })),
      api.recommendations().catch(() => null),
      api.playlists().catch(() => ({ playlists: [], suggestions: [] })),
      api.library({ favorites: true, limit: 40 }).catch(() => ({ items: [] as Game[], total: 0 })),
      api.library({ recent: true, limit: 20 }).catch(() => ({ items: [] as Game[], total: 0 })),
      api.library({ limit: 80 }).catch(() => ({ items: [] as Game[], total: 0 })),
    ])
    const game = (g: Game): Tile => ({ key: `g${g.id}`, title: g.title, cover: g.coverUrl, gameId: g.id,
      subtitle: [g.year, g.genre].filter(Boolean).join(' · ') || undefined })
    const picks = recs?.picks ?? []
    const out: Rail[] = [{ title: 'Hello' + (me ? `, ${me.name}` : ''), tiles: [
      { key: 'surprise', title: '🎲 Surprise me', subtitle: 'A random pick for you', action: () => {
        const pool = picks.length ? picks : lib.items.map((g) => ({ title: g.title, gameId: g.id, catalog: null, coverUrl: g.coverUrl }))
        const p = pool[Math.floor(Math.random() * pool.length)]
        if (p) { setOpen({ key: 'surprise-pick', title: p.title, gameId: p.gameId, catalog: p.catalog, cover: p.coverUrl ?? titleArt(p.title, p.catalog) }); setOpenIx(0) }
      } },
      ...(who && who.profiles.length > 1 ? [{ key: 'who', title: '👪 Switch player', subtitle: me?.name, action: () => setAsking(true) }] : []),
      { key: 'exit', title: '⏏ Exit TV mode', action: () => { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); navigate('/') } },
    ] }]
    if (saves.saves.length) out.push({ title: '▶ Continue', tiles: saves.saves.map((s) => ({ key: `s${s.gameId}`, title: s.title, cover: s.thumb ? `${s.thumb}?profile=${currentProfile()}` : s.coverUrl, gameId: s.gameId, resume: true, subtitle: s.device ? `saved on ${s.device}` : 'saved' })) })
    if (picks.length) out.push({ title: '✨ For you', tiles: picks.map((p) => ({ key: `r${p.title}`, title: p.title, gameId: p.gameId, catalog: p.catalog,
      cover: p.coverUrl ?? titleArt(p.matchedTitle ?? p.title, p.catalog), subtitle: p.reason })) })
    if (lists.playlists.length) out.push({ title: '🎉 Playlists', tiles: lists.playlists.map((l) => ({ key: `p${l.id}`, title: l.name, cover: l.covers[0], playlistId: l.id, subtitle: `${l.count} games` })) })
    if (favs.items.length) out.push({ title: '★ Favorites', tiles: favs.items.map(game) })
    if (recent.items.length) out.push({ title: '🕘 Recently played', tiles: recent.items.map(game) })
    out.push({ title: '📚 Library', tiles: lib.items.map(game) })
    setRails(out)
  }, [me?.name, who]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!asking) load() }, [asking, load])

  // ---- actions
  const openPlaylist = async (id: number) => {
    try {
      const p = await api.playlist(id)
      setRails((r) => [r[0], { title: `🎉 ${p.name}`, tiles: p.items.map((it, i) => ({ key: `pi${i}`, title: it.title, gameId: it.gameId, catalog: it.catalog,
        cover: it.coverUrl ?? titleArt(it.matchedTitle ?? it.title, it.catalog), subtitle: [it.players && `👥 ${it.players}`, it.note].filter(Boolean).join(' · ') })) }, ...r.slice(1)])
      setPos({ row: 1, col: 0 })
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const select = (t: Tile) => {
    if (t.action) return t.action()
    if (t.playlistId) return openPlaylist(t.playlistId)
    setOpen(t)
    setOpenIx(0)
  }
  const resolveId = async (t: Tile) => t.gameId ?? (t.catalog ? (await api.catalogFetch(t.catalog)).gameId : null)
  const playHere = async (t: Tile) => {
    try {
      const id = await resolveId(t)
      if (id) navigate(`/emulate/${id}?tv=1`)
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const playOnC64 = async (t: Tile) => {
    try {
      if (t.gameId) await api.play(t.gameId)
      else if (t.catalog) await api.catalogPlay(t.catalog)
      toast(`📺 Loading ${t.title} on your C64 — switch the TV to the C64`, 'ok')
      setOpen(null)
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const overlayButtons = open ? [
    { label: open.resume ? '▶ Continue here' : '💻 Play here', run: () => playHere(open) },
    ...(open.gameId || open.catalog ? [{ label: '📺 Play on the C64', run: () => playOnC64(open) }] : []),
    ...(open.gameId ? [{ label: '👍 I like this', run: () => api.rate(open.title, 1, open.gameId).then(() => toast('👍 Noted', 'ok')) }] : []),
    { label: '← Back', run: () => setOpen(null) },
  ] : []

  // ---- input: keyboard, TV remote (arrow keys) and gamepads
  const move = useCallback((dir: 'up' | 'down' | 'left' | 'right' | 'ok' | 'back') => {
    if (asking) {
      const n = who?.profiles.length ?? 0
      if (dir === 'left' || dir === 'up') setWhoIx((i) => Math.max(0, i - 1))
      else if (dir === 'right' || dir === 'down') setWhoIx((i) => Math.min(n - 1, i + 1))
      else if (dir === 'ok' && who) pickWho(who.profiles[whoIx])
      return
    }
    if (open) {
      if (dir === 'left' || dir === 'up') setOpenIx((i) => Math.max(0, i - 1))
      else if (dir === 'right' || dir === 'down') setOpenIx((i) => Math.min(overlayButtons.length - 1, i + 1))
      else if (dir === 'ok') overlayButtons[openIx]?.run()
      else if (dir === 'back') setOpen(null)
      return
    }
    setPos(({ row, col }) => {
      if (dir === 'up') row = Math.max(0, row - 1)
      if (dir === 'down') row = Math.min(rails.length - 1, row + 1)
      if (dir === 'left') col = Math.max(0, col - 1)
      if (dir === 'right') col = col + 1
      const n = rails[row]?.tiles.length ?? 1
      col = Math.min(col, n - 1)
      if (dir === 'ok') { const t = rails[row]?.tiles[col]; if (t) window.setTimeout(() => select(t), 0) }
      return { row, col }
    })
  }, [asking, open, openIx, rails, overlayButtons.length, who, whoIx]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const map: Record<string, Parameters<typeof move>[0]> = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
        Enter: 'ok', ' ': 'ok', Escape: 'back', Backspace: 'back', BrowserBack: 'back' }
      const d = map[e.key]
      if (!d) return
      e.preventDefault()
      move(d)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [move])
  useEffect(() => {
    let raf = 0
    let last = 0
    let prev = ''
    const tick = (t: number) => {
      const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) as Gamepad[] : []
      let d: Parameters<typeof move>[0] | null = null
      for (const p of pads) {
        const b = (i: number) => !!p.buttons[i]?.pressed
        const ax = p.axes[0] ?? 0, ay = p.axes[1] ?? 0
        if (b(12) || ay < -0.6) d = 'up'
        else if (b(13) || ay > 0.6) d = 'down'
        else if (b(14) || ax < -0.6) d = 'left'
        else if (b(15) || ax > 0.6) d = 'right'
        else if (b(0) || b(9)) d = 'ok'
        else if (b(1) || b(8)) d = 'back'
        if (d) break
      }
      const key = d ?? ''
      const isMove = d && d !== 'ok' && d !== 'back'
      if (d && (key !== prev || (isMove && t - last > REPEAT_MS * 2))) { move(d); last = t }
      prev = key
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [move])

  // keep the focused tile in view
  useEffect(() => {
    const rail = railRefs.current[pos.row]
    rail?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    const tile = rail?.querySelectorAll<HTMLElement>('.tv-tile')[pos.col]
    tile?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' })
  }, [pos])

  const pickWho = (p: Profile | undefined) => {
    if (!p) return
    setCurrentProfile(p.id)
    try { sessionStorage.setItem('c64.tv.who', '1') } catch { /* ignore */ }
    window.location.reload()
  }
  const goFull = () => { if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {}) }

  if (asking && who) {
    return (
      <div className="tv" onClick={goFull}>
        <WhoIsPlaying big data={who} onChanged={() => api.profiles().then(setWho)} focusId={who.profiles[whoIx]?.id} afterPick={pickWho} />
        <p className="tv-hint">🎮 D-pad choose · A select — or click / tap</p>
      </div>
    )
  }

  return (
    <div className="tv" onClick={goFull}>
      <header className="tv-head">
        <span className="tv-brand"><span className="stripes"><i /><i /><i /><i /></span> C64 Ultimate</span>
        <span className="tv-hint">🎮 D-pad move · A select · B back</span>
        {me && <span className="tv-me"><span className="profile-emoji" style={{ background: me.color }}>{me.emoji}</span> {me.name}</span>}
        <span className="tv-clock">{clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
      </header>
      {rails.map((r, ri) => (
        <section key={r.title} className="tv-rail" ref={(el) => { railRefs.current[ri] = el }}>
          <h2>{r.title}</h2>
          <div className="tv-row">
            {r.tiles.map((t, ci) => {
              const focused = pos.row === ri && pos.col === ci && !open
              return (
                <button key={t.key} className={`tv-tile ${t.action ? 'tv-action' : ''} ${focused ? 'focus' : ''}`}
                  onClick={() => { setPos({ row: ri, col: ci }); select(t) }}>
                  {!t.action && <CoverArt game={{ id: t.gameId ?? 0, title: t.title, coverUrl: t.cover ?? null, format: '' } as unknown as Game} />}
                  <strong>{t.title}</strong>
                  {t.subtitle && <span className="tv-sub">{t.subtitle}</span>}
                </button>
              )
            })}
          </div>
        </section>
      ))}
      {open && (
        <div className="tv-overlay" onClick={() => setOpen(null)}>
          <div className="tv-detail" onClick={(e) => e.stopPropagation()}>
            <CoverArt game={{ id: open.gameId ?? 0, title: open.title, coverUrl: open.cover ?? null, format: '' } as unknown as Game} size="lg" />
            <div>
              <h1>{open.title}</h1>
              {open.subtitle && <p className="tv-sub">{open.subtitle}</p>}
              <div className="tv-buttons">
                {overlayButtons.map((b, i) => (
                  <button key={b.label} className={`tv-btn ${openIx === i ? 'focus' : ''}`} onClick={b.run}>{b.label}</button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
