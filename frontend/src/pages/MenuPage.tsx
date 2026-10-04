import { useCallback, useEffect, useState, Suspense, lazy } from 'react'
import { Card, Empty, Spinner } from '../components/common'
import { MenuScreenView } from '../components/MenuScreenView'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { MenuScreen } from '../shared/types'

// xterm.js is large; load the remote menu only when it is shown.
const RemoteMenu = lazy(() => import('../components/RemoteMenu').then((m) => ({ default: m.RemoteMenu })))

interface LogLine { id: number; action: string; changed: boolean; verified: boolean; note: string; selected?: string | null }

const NAV: { action: string; label: string }[] = [
  { action: 'page_up', label: 'Page ▲' }, { action: 'up', label: '▲' }, { action: 'home', label: 'Home' },
  { action: 'left', label: '◀' }, { action: 'return', label: 'RETURN' }, { action: 'right', label: '▶' },
  { action: 'page_down', label: 'Page ▼' }, { action: 'down', label: '▼' }, { action: 'back', label: 'Back' },
]

export function MenuPage() {
  const { status } = useLive()
  const toast = useToast()
  const [screen, setScreen] = useState<MenuScreen | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [auto, setAuto] = useState(false)
  const [log, setLog] = useState<LogLine[]>([])
  const caps = status?.capabilities
  const canRead = caps?.details.menuScreen?.state !== 'unsupported'
  const canNav = status?.inputMode === 'rest'

  const refresh = useCallback(async () => {
    if (!canRead) return
    try {
      const r = await api.menuRead()
      setOpen(r.open)
      setScreen(r.screen)
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }, [canRead, toast])

  useEffect(() => { if (status?.connected) refresh() }, [status?.connected, refresh])
  useEffect(() => {
    if (!auto) return
    const t = window.setInterval(refresh, 1500)
    return () => window.clearInterval(t)
  }, [auto, refresh])

  const act = async (action: string) => {
    setBusy(true)
    try {
      const r = await api.menu(action)
      setLog((l) => [{ id: Date.now(), action, changed: !!r.changed, verified: !!r.verified, note: r.note ?? '',
        selected: r.after?.selectedText ?? r.screen?.selectedText }, ...l].slice(0, 12))
      if (r.screen !== undefined) setScreen(r.screen)
      if (action === 'open') setOpen(true)
      if (action === 'close' || action === 'exit') await refresh()
      if (r.screen === undefined && canRead) await refresh()
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  // Firmware without menu_screen/REST input (e.g. 1.1.0s2): the Telnet remote menu is the full UI.
  const telnet = !!status?.connected && !status?.simulated && !!caps?.usable.telnet
  if (telnet && !(canRead && canNav)) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Ultimate menu</h1>
          <span className="muted">live remote control over Telnet</span>
        </div>
        <Card><Suspense fallback={<Spinner />}><RemoteMenu /></Suspense></Card>
        <p className="muted small">
          This is the Ultimate's own menu: browse SD/USB/Flash, mount and run files (RETURN opens the file menu), change
          settings (F2) or search Assembly64/CommoServe (F6). It runs alongside the C64, so you can keep the Display open
          in another tab. Settings changed here are not saved unless you save them in the menu.
        </p>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Ultimate menu</h1>
        <div className="row-actions">
          <button className="btn btn-primary" disabled={busy || !status?.connected} onClick={() => act('open')}>Open menu</button>
          <button className="btn" disabled={busy || !status?.connected} onClick={() => act('close')}>Close menu</button>
          <button className="btn btn-ghost" disabled={!canRead || !status?.connected} onClick={refresh}>Refresh</button>
          <label className="toggle"><input type="checkbox" checked={auto} disabled={!canRead} onChange={(e) => setAuto(e.target.checked)} /> Auto-refresh</label>
        </div>
      </div>
      {!canRead && (
        <div className="banner banner-warn">
          This firmware does not provide <code>GET /v1/machine:menu_screen</code>. The menu can be toggled, but its
          contents can’t be shown or verified here.
        </div>
      )}
      {canRead && caps?.details.menuScreen?.state === 'unknown' && (
        <div className="banner banner-info">Menu-screen support has not been confirmed yet — it will be once the menu is open and read successfully.</div>
      )}

      <div className="menu-layout">
        <Card title={screen ? screen.title || 'Menu' : 'Menu screen'}>
          {screen ? <MenuScreenView screen={screen} /> : <Empty>{open ? 'Reading…' : 'The Ultimate menu is closed.'}</Empty>}
          {screen && (
            <div className="muted small">
              Selected: <strong>{screen.selectedText ?? 'none detected'}</strong> · encoding {screen.encoding} · #{screen.hash}
            </div>
          )}
        </Card>
        <div className="menu-side">
          <Card title="Navigate">
            {!canNav && <p className="muted small">Menu navigation needs the REST input API.</p>}
            <div className="menu-pad">
              {NAV.map((n) => (
                <button key={n.action} className="btn menu-key" disabled={busy || !canNav || !open} onClick={() => act(n.action)}>{n.label}</button>
              ))}
            </div>
            <button className="btn btn-ghost full" disabled={busy || !canNav || !open} onClick={() => act('exit')}>RUN/STOP (exit)</button>
            <p className="muted small">Each key is sent once, then the screen is re-read to verify it changed. Nothing is repeated automatically.</p>
          </Card>
          <Card title="Verification log">
            {log.length ? (
              <ul className="feed">
                {log.map((l) => (
                  <li key={l.id} className={l.changed || !l.verified ? '' : 'feed-warn'}>
                    <span className="feed-src">{l.action}</span>
                    <span className="feed-op">{l.verified ? (l.changed ? 'changed ✓' : 'no change') : 'unverified'}</span>
                    {l.selected && <span className="muted"> → {l.selected}</span>}
                    {l.note && <div className="muted small">{l.note}</div>}
                  </li>
                ))}
              </ul>
            ) : <Empty>No actions yet.</Empty>}
          </Card>
        </div>
      </div>
    </div>
  )
}
