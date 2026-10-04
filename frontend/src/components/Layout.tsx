import { useCallback, useEffect, useRef, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { useGamepad } from '../hooks/useGamepad'
import { useLive } from '../hooks/useLive'
import { input } from '../controllers/inputQueue'
import { useToast } from './Toasts'
import { Dot } from './common'
import { ShareButton } from './ShareAccess'
import { ProfileSwitcher } from './ProfileSwitcher'
import { newsSeenAt } from '../pages/NewsPage'
import { SideNav } from './SideNav'
import { PasswordNudge } from './PasswordNudge'
import { Tour } from './Tour'


export function Layout() {
  const { status, socket, news } = useLive()
  const toast = useToast()
  const tone = !status ? 'idle' : status.connected ? 'ok' : status.configured ? 'bad' : 'warn'
  const label = !status ? 'Loading…' : status.connected
    ? (status.info?.hostname || status.host) : status.configured ? 'Disconnected' : 'Not configured'

  // Keyboard/joystick pages only appear when input can actually reach the C64: the REST input API,
  // or (joystick only) an ESP32 joystick bridge wired into the joystick port.
  const canJoystick = !!status?.connected && !!status?.input?.joystickSupported
  const canInput = status?.inputMode === 'rest' || canJoystick

  const navigate = useNavigate()
  const onPad = useCallback((name: string) => toast('🎮 ' + name + ' connected — D-pad to move, A to select, B back, Start = Display', 'ok'), [toast])
  useGamepad({ joystickMode: canJoystick, joystickPort: status?.input?.joystickPort ?? 2,
    singleFire: status?.input?.joystickVia === 'bridge', onConnect: onPad })
  const location = useLocation()
  const stale = useRef(false)

  // 🆕 badge: news, new releases and videos found since you last opened What's new
  const [fresh, setFresh] = useState(0)
  const onNews = location.pathname === '/news'
  useEffect(() => {
    if (onNews) { setFresh(0); return }
    api.newsCount(newsSeenAt()).then((r) => setFresh(r.total)).catch(() => {})
  }, [onNews, news?.at])
  useEffect(() => {
    if (news?.added && !onNews) toast(`🆕 ${news.added} new in the C64 world: ${news.titles.slice(0, 2).join(' · ')}`, 'ok')
  }, [news?.at]) // eslint-disable-line react-hooks/exhaustive-deps

  // After the app is rebuilt, a tab that is already open keeps running the old code. Detect a
  // newer build and load it on the next page change (e.g. Play → Display), so fixes apply without F5.
  useEffect(() => {
    const mine = document.querySelector<HTMLScriptElement>('script[src*="/assets/index-"]')?.src.split('/').pop()
    if (!mine) return // dev server
    const check = () => api.health().then((h) => {
      if (h.frontendBuild && h.frontendBuild !== mine) stale.current = true
    }).catch(() => {})
    check()
    const t = window.setInterval(check, 30000)
    window.addEventListener('focus', check)
    return () => { window.clearInterval(t); window.removeEventListener('focus', check) }
  }, [])
  useEffect(() => {
    if (stale.current) window.location.reload()
  }, [location.pathname])

  // "/" from any page: focus the command bar on this page, or go Home and focus it there.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
      if (window.location.pathname.startsWith('/emulate/')) return
      const el = document.activeElement as HTMLElement | null
      if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return
      e.preventDefault()
      const bar = document.querySelector<HTMLInputElement>('[data-command-input]')
      if (bar) bar.focus()
      else navigate('/', { state: { focusCommand: Date.now() } })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  const releaseAll = async () => {
    await input.releaseAll()
    toast('All inputs released', 'ok')
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-stripes"><i /><i /><i /><i /></div>
          <div>
            <div className="brand-name">C64 Ultimate</div>
            <div className="brand-sub">AI Console</div>
          </div>
        </div>
        <SideNav online={!!status?.connected} canInput={canInput} fresh={fresh} />
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="conn">
            <Dot tone={tone} />
            <span className="conn-host">{label}</span>
            {status?.simulated && <span className="badge badge-unverified">SIMULATED · {status.simulatorProfile}</span>}
            {status?.connected && status.info?.firmwareVersion && (
              <span className="muted hide-sm">fw {status.info.firmwareVersion}</span>
            )}
            {status?.connected && (
              <span className={`badge ${status.inputMode === 'rest' ? 'badge-supported' : status.inputMode === 'legacy' ? 'badge-unverified' : 'badge-unsupported'}`}
                title="Keyboard/joystick input method">
                input: {status.inputMode}
              </span>
            )}
            {status?.input?.bridge?.configured && (
              <span className={`badge ${status.input.bridge.online ? 'badge-supported' : 'badge-unsupported'}`}
                title={status.input.bridge.online ? `Joystick bridge ${status.input.bridge.name ?? ''} (${status.input.bridge.rttMs ?? '?'} ms)` : 'Joystick bridge is not responding'}>
                🕹 {status.input.bridge.online ? 'joystick bridge' : 'bridge offline'}
              </span>
            )}
            {socket !== 'open' && <span className="badge badge-unknown" title="Live updates">live: {socket}</span>}
          </div>
          <div className="topbar-actions">
          <a className="btn btn-ghost btn-sm hide-sm" href="/tv" title="📺 TV / couch mode: full screen, gamepad or remote">📺 TV</a>
          <ProfileSwitcher />
          <ShareButton />
          {canInput && <button className="btn btn-emergency" onClick={releaseAll} title="Release every held key and joystick input">
            RELEASE ALL
          </button>}
          </div>
        </header>
        <main className="content">
          <PasswordNudge />
          <Outlet />
        </main>
        <Tour />
      </div>
    </div>
  )
}
