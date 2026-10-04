import { useEffect, useState } from 'react'
import { FirmwareNotice } from '../components/FirmwareNotice'
import { useNavigate } from 'react-router-dom'
import { CommandBar } from '../components/CommandBar'
import { ActivityFeed, Card, LaunchProgress, Modal, Stat } from '../components/common'
import { CoverArt } from '../components/CoverArt'
import { ForYou } from '../components/ForYou'
import { YourWeek } from '../components/YourWeek'
import { BROWSER_TIP, C64_TIP, canPlayInBrowser } from '../components/PlayChoice'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { Game } from '../shared/types'

export function Dashboard() {
  const { status, audit, job, session } = useLive()
  const toast = useToast()
  const navigate = useNavigate()
  const [ai, setAi] = useState<{ provider: string; model: string; configured: boolean; local: boolean } | null>(null)
  const [recent, setRecent] = useState<Game[]>([])
  const [modal, setModal] = useState<'power' | 'disk' | 'play' | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    api.ai().then(setAi).catch(() => {})
    api.library({ recent: true, limit: 8 }).then((r) => setRecent(r.items)).catch(() => {})
  }, [job?.status])

  const [target, setTarget] = useState<'c64' | 'browser'>('c64')
  useEffect(() => { if (status && !status.connected) setTarget('browser') }, [status?.connected]) // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn()
      toast(label, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const toggleMenu = () => act(menuOpen ? 'Menu closed' : 'Menu opened', async () => {
    const r = await api.menu(menuOpen ? 'close' : 'open')
    setMenuOpen(!menuOpen)
    if (r?.note) toast(r.note, 'info')
  })

  const cur = session ?? status?.session
  const drives = (status?.drives ?? []).filter((d) => d.id.length === 1)
  const caps = status?.capabilities
  const connected = !!status?.connected

  return (
    <div className="page dashboard">
      <FirmwareNotice />
      {!status?.configured && status && (
        <div className="banner banner-warn">
          No C64 Ultimate configured. <button className="link" onClick={() => navigate('/setup')}>Run the setup wizard</button>
        </div>
      )}
      {status?.configured && !connected && (
        <div className="banner banner-bad">
          Can’t reach the C64 Ultimate: {status.lastError}.{' '}
          <button className="link" onClick={() => act('Reconnected', api.reconnect)}>Retry</button>
        </div>
      )}

      <CommandBar context={{ menuOpen }} />

      <div className="console-buttons">
        <button className="cbtn cbtn-play" onClick={() => setModal('play')} disabled={!connected}>
          <span className="cbtn-icon">▶</span>PLAY
        </button>
        <button className="cbtn cbtn-reset" onClick={() => act('C64 reset', api.reset)} disabled={!connected}>
          <span className="cbtn-icon">⟲</span>RESET
        </button>
        <button className="cbtn cbtn-menu" onClick={toggleMenu} disabled={!connected}>
          <span className="cbtn-icon">☰</span>MENU
        </button>
        <button className="cbtn cbtn-disk" onClick={() => setModal('disk')} disabled={!connected}>
          <span className="cbtn-icon">◉</span>DISK
        </button>
        {(status?.inputMode === 'rest' || !!status?.input?.joystickSupported) && <button className="cbtn cbtn-joy" onClick={() => navigate('/controller')} disabled={!connected}>
          <span className="cbtn-icon">✥</span>JOYSTICK
        </button>}
        {status?.inputMode === 'rest' && <button className="cbtn cbtn-kbd" onClick={() => navigate('/controller#keyboard')} disabled={!connected}>
          <span className="cbtn-icon">⌨</span>KEYBOARD
        </button>}
        <button className="cbtn cbtn-display" onClick={() => navigate('/stream')} disabled={!connected}>
          <span className="cbtn-icon">▣</span>DISPLAY
        </button>
        <button className="cbtn cbtn-power" onClick={() => setModal('power')} disabled={!connected || !caps?.usable.machinePowerOff}>
          <span className="cbtn-icon">⏻</span>POWER
        </button>
      </div>

      <ForYou />
      <YourWeek />

      <div className="grid-3">
        <Card title="Machine">
          <div className="stats">
            <Stat label="Status" value={connected ? 'Connected' : status?.configured ? 'Offline' : 'Not set up'} />
            <Stat label="Hostname" value={status?.info?.hostname || status?.host} />
            <Stat label="Product" value={status?.info?.product} />
            <Stat label="Firmware" value={status?.info?.firmwareVersion} hint={status?.apiVersion ? `REST API ${status.apiVersion}` : undefined} />
            <Stat label="FPGA / Core" value={[status?.info?.fpgaVersion, status?.info?.coreVersion].filter(Boolean).join(' / ') || undefined} />
            <Stat label="AI model" value={ai?.configured ? ai.model : 'None (rules only)'} hint={ai?.configured ? `${ai.provider}${ai.local ? ' · local' : ''}` : undefined} />
          </div>
        </Card>
        <Card title="Now playing">
          {cur?.title ? (
            <div className="now">
              <div className="now-title">{cur.title}</div>
              <div className="muted">{cur.format?.toUpperCase()}{cur.diskCount > 1 ? ` · disk ${cur.currentDisk} of ${cur.diskCount}` : ''}</div>
              {cur.diskCount > 1 && (
                <div className="disk-pills">
                  {cur.disks.map((d) => (
                    <button key={d.mediaId} className={`pill ${d.diskNumber === cur.currentDisk ? 'on' : ''}`}
                      onClick={() => act(`Disk ${d.diskNumber} inserted`, () => api.sessionDisk(d.diskNumber))}>
                      Disk {d.diskNumber}{d.label ? ` · ${d.label}` : ''}
                    </button>
                  ))}
                </div>
              )}
              {cur.gameId && <button className="btn btn-ghost" onClick={() => navigate(`/games/${cur.gameId}`)}>Details →</button>}
            </div>
          ) : <div className="muted">Nothing launched yet. Try “Play Bruce Lee”.</div>}
          <LaunchProgress job={job} />
        </Card>
        <Card title="Drives & input">
          <ul className="drive-list">
            {drives.map((d) => (
              <li key={d.id}>
                <span className={`drive-led ${d.enabled ? (d.mounted ? 'on' : 'idle') : 'off'}`} />
                <strong>Drive {d.id.toUpperCase()}</strong> <span className="muted">#{d.busId} · {d.type}</span>
                <div className="drive-image">{d.enabled ? (d.imageFile || 'empty') : 'off'}</div>
              </li>
            ))}
            {!drives.length && <li className="muted">No drive information.</li>}
          </ul>
          <div className="stats">
            <Stat label="Input mode" value={status?.inputMode} hint={status?.inputMode === 'legacy' ? (status?.input?.joystickVia === 'bridge' ? 'keyboard buffer · joystick via bridge' : 'keyboard buffer only · no joystick') : undefined} />
            <Stat label="Joystick port" value={status?.input?.joystickPort} hint={status?.input?.joystickVia === 'bridge' ? 'joystick bridge' : status?.input?.joystickSupported ? 'REST joystick' : 'not available'} />
            <Stat label="Held inputs" value={status?.input?.held.length ?? 0} />
          </div>
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Recently played" actions={<button className="btn btn-ghost" onClick={() => navigate('/library')}>Library →</button>}>
          {recent.length ? (
            <div className="shelf">
              {recent.map((g) => (
                <button key={g.id} className="shelf-item" onClick={() => navigate(`/games/${g.id}`)}>
                  <CoverArt game={g} />
                  <span>{g.title}</span>
                </button>
              ))}
            </div>
          ) : <div className="muted">No games played yet.</div>}
        </Card>
        <Card title="Recent actions" actions={<button className="btn btn-ghost" onClick={() => navigate('/troubleshooting')}>All →</button>}>
          <ActivityFeed entries={audit} />
        </Card>
      </div>

      <Modal open={modal === 'power'} title="Power off the C64 Ultimate?" onClose={() => setModal(null)} footer={
        <>
          <button className="btn" onClick={() => setModal(null)}>Cancel</button>
          <button className="btn btn-danger" onClick={() => { setModal(null); act('Power off sent', api.powerOff) }}>Power off</button>
        </>
      }>
        <p>This sends <code>PUT /v1/machine:poweroff</code>. You will need physical access to turn it back on.</p>
      </Modal>

      <Modal open={modal === 'disk'} title="Disk" onClose={() => setModal(null)}>
        {cur?.diskCount ? (
          <>
            <p>{cur.title} — {cur.diskCount} disk{cur.diskCount > 1 ? 's' : ''}</p>
            <div className="disk-pills">
              {cur.disks.map((d) => (
                <button key={d.mediaId} className={`pill ${d.diskNumber === cur.currentDisk ? 'on' : ''}`}
                  onClick={() => act(`Disk ${d.diskNumber} inserted`, () => api.sessionDisk(d.diskNumber))}>
                  Disk {d.diskNumber}
                </button>
              ))}
            </div>
            <div className="row-actions">
              <button className="btn" onClick={() => act('Previous disk', api.previousDisk)}>◀ Previous</button>
              <button className="btn" onClick={() => act('Next disk', api.nextDisk)}>Next ▶</button>
            </div>
          </>
        ) : <p className="muted">No disk game is active. Start one from the library.</p>}
        <hr />
        <div className="row-actions">
          {drives.filter((d) => d.mounted).map((d) => (
            <button key={d.id} className="btn" onClick={() => act(`Drive ${d.id.toUpperCase()} ejected`, () => api.driveAction(d.id, 'remove'))}>
              Eject drive {d.id.toUpperCase()}
            </button>
          ))}
        </div>
      </Modal>

      <Modal open={modal === 'play'} title="Play" onClose={() => setModal(null)}>
        <div className="seg play-target" role="radiogroup" aria-label="Where to play">
          <button role="radio" aria-checked={target === 'c64'} className={`seg-btn ${target === 'c64' ? 'on' : ''}`}
            disabled={!status?.connected} title={C64_TIP} onClick={() => setTarget('c64')}>📺 On my C64</button>
          <button role="radio" aria-checked={target === 'browser'} className={`seg-btn ${target === 'browser' ? 'on' : ''}`}
            title={BROWSER_TIP} onClick={() => setTarget('browser')}>💻 In browser</button>
        </div>
        {recent.length ? (
          <div className="shelf">
            {recent.map((g) => (
              <button key={g.id} className="shelf-item" disabled={target === 'browser' && !canPlayInBrowser(g.format, g.category)}
                onClick={() => {
                  setModal(null)
                  if (target === 'browser') navigate(`/emulate/${g.id}`)
                  else act(`📺 Loading ${g.title} on your C64`, async () => { await api.play(g.id); navigate('/stream') })
                }}>
                <CoverArt game={g} />
                <span>{g.title}</span>
              </button>
            ))}
          </div>
        ) : <p className="muted">No recent games.</p>}
        <div className="row-actions"><button className="btn btn-primary" onClick={() => navigate('/library')}>Browse library</button></div>
      </Modal>
    </div>
  )
}
