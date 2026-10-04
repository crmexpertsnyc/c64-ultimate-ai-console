import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Empty, LaunchProgress, Spinner } from '../components/common'
import { DisplaySurface } from '../components/DisplaySurface'
import { LivePanel } from '../components/LivePanel'
import { CurrentControls } from '../components/ControlsCard'
import { useToast } from '../components/Toasts'
import { LatencyReadout } from '../components/VideoCanvas'
import type { VideoStats } from '../components/VideoCanvas'
import { Joystick } from '../controllers/Joystick'
import { RealAssist } from '../components/RealAssist'
import { Keyboard } from '../controllers/Keyboard'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import { PcmPlayer, setSoundPreferred, soundPreferred } from '../services/pcmPlayer'

// xterm.js is large; load the remote menu only when it is shown.
const RemoteMenu = lazy(() => import('../components/RemoteMenu').then((m) => ({ default: m.RemoteMenu })))

export function StreamPage() {
  const { status, vision, job, session } = useLive()
  const toast = useToast()
  const navigate = useNavigate()
  const caps = status?.capabilities
  const simulated = !!status?.simulated
  const canVideo = !!status?.connected && (simulated || !!caps?.usable.videoStream)
  const canAudio = !!status?.connected && !simulated && !!caps?.usable.audioStream
  // On-screen controls only make sense when input can actually reach the C64.
  const canKeyboard = !!status?.connected && status?.inputMode === 'rest'
  const canJoystick = !!status?.connected && !!status?.input?.joystickSupported
  const viaBridge = status?.input?.joystickVia === 'bridge'
  const canControl = canKeyboard || canJoystick
  const canTelnet = !!status?.connected && !simulated && !!caps?.usable.telnet

  const [videoOn, setVideoOn] = useState(true)
  const [videoKey, setVideoKey] = useState(() => Date.now())
  const [audioOn, setAudioOn] = useState(false)
  const [soundBlocked, setSoundBlocked] = useState(false)
  const [showMenu, setShowMenu] = useState(false)
  const [showLatency, setShowLatency] = useState(() => {
    try { return localStorage.getItem('c64.display.latency') === 'on' } catch { return false }
  })
  const [stats, setStats] = useState<VideoStats | null>(null)
  const [mjpegFallback, setMjpegFallback] = useState(false)
  const [visionState, setVisionState] = useState<any>(null)
  const [goal, setGoal] = useState('press whatever starts the game')
  const player = useRef<PcmPlayer | null>(null)
  const audioAutoStarted = useRef(false)

  const toggleLatency = () => setShowLatency((v) => {
    try { localStorage.setItem('c64.display.latency', v ? 'off' : 'on') } catch { /* ignored */ }
    return !v
  })

  // The picture and sound connect over WebSockets; the server starts the Ultimate's streams for
  // the first viewer and stops them after the last one leaves, so nothing to start/stop here.
  const startAudio = () => {
    player.current?.stop()
    player.current = new PcmPlayer()
    player.current.start()
    setAudioOn(true)
    // If the browser still blocks autoplay after a moment, offer a one-click unmute.
    window.setTimeout(() => setSoundBlocked(!!player.current?.blocked), 300)
  }

  const stopAudio = () => {
    player.current?.stop()
    player.current = null
    setAudioOn(false)
    setSoundBlocked(false)
  }

  useEffect(() => {
    if (audioAutoStarted.current || !canAudio) return
    audioAutoStarted.current = true
    if (soundPreferred()) startAudio()
  }, [canAudio])

  useEffect(() => {
    api.vision().then(setVisionState).catch(() => {})
    return () => {
      player.current?.stop()
      player.current = null
    }
  }, [])
  useEffect(() => { if (vision) setVisionState(vision) }, [vision])

  const toggleSound = async () => {
    try {
      if (audioOn && soundBlocked) {
        setSoundBlocked(!(await player.current?.resume()))
        return
      }
      if (audioOn) {
        setSoundPreferred(false)
        stopAudio()
      } else {
        setSoundPreferred(true)
        startAudio()
      }
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  // ⟲ Reset: reboots the real C64 (the game in memory is lost, so a running game asks first)
  const [resetting, setResetting] = useState(false)
  const resetC64 = async () => {
    if (session?.title && !window.confirm(`Reset the C64? ${session.title} will stop (unsaved progress is lost).`)) return
    setResetting(true)
    try {
      await api.reset()
      toast('⟲ C64 reset — back at the start screen', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setResetting(false)
    }
  }

  const port = status?.input?.joystickPort ?? 2
  const vs = visionState?.session
  const recentJob = job && (job.status === 'running' || (job.finishedAt && Date.now() / 1000 - job.finishedAt < 20)) ? job : null

  return (
    <div className="page">
      <div className="page-head">
        <span className="mode-pill mode-c64" title="This is your real Commodore 64 Ultimate, live">📺 On my C64</span>
        <h1>{session?.title || 'C64 Screen'}</h1>
        {session?.title && session.diskCount > 1 && <span className="muted">disk {session.currentDisk} of {session.diskCount}</span>}
        <div className="row-actions">
          {session?.gameId && (
            <button className="btn btn-ghost" onClick={() => navigate(`/emulate/${session.gameId}`)}
              title="Play this game in an emulator on this device instead (works away from home)">💻 Play in browser instead</button>
          )}
          <PowerButtons connected={!!status?.connected} canOff={!!caps?.usable.machinePowerOff} />
          {status?.connected && (
            <button className="btn btn-reset" onClick={resetC64} disabled={resetting}
              title="Reboot the real C64 (like its reset button) — back to the BASIC start screen">
              {resetting ? '⟲ Resetting…' : '⟲ Reset C64'}</button>
          )}
          {canAudio && (
            <button className={`btn ${soundBlocked ? 'btn-primary' : audioOn ? '' : 'btn-ghost'}`} onClick={toggleSound}
              aria-pressed={audioOn && !soundBlocked}
              title={audioOn ? 'Mute this page (the C64 keeps playing on your TV)' : 'Unmute: hear the C64 here too — muted by default, since your TV usually plays it already'}>
              {soundBlocked ? '🔈 Click to enable sound' : audioOn ? '🔊 Mute' : '🔇 Muted — unmute'}
            </button>
          )}
          {videoOn && !mjpegFallback && (
            <button className={`btn ${showLatency ? 'btn-primary' : ''}`} onClick={toggleLatency} title="Show measured display delay">
              ⏱ Latency
            </button>
          )}
          {canTelnet && (
            <button className={`btn ${showMenu ? 'btn-primary' : ''}`} onClick={() => setShowMenu((v) => !v)}
              title="Browse, mount and run files with the Ultimate's own menu">
              ☰ Ultimate menu
            </button>
          )}
          <button className="btn btn-ghost" disabled={!canVideo}
            onClick={() => { setVideoOn((v) => !v); setVideoKey(Date.now()) }}>
            {videoOn ? '■ Stop video' : '▶ Start video'}
          </button>
        </div>
      </div>
      {simulated && <div className="banner banner-info">Simulation mode: the picture is a rendering of the simulated text screen; there is no audio.</div>}
      {!simulated && caps && !caps.usable.videoStream && (
        <div className="banner banner-warn">This device did not report the stream API. Video/audio streaming is only available on Ultimate 64-class hardware.</div>
      )}

      {recentJob && <LaunchProgress job={recentJob} />}
      {canVideo && <LivePanel />}
      <RealAssist connected={!!status?.connected} />
      {showLatency && videoOn && !mjpegFallback && <Card title="Display latency"><LatencyReadout stats={stats} /></Card>}

      <div className={showMenu ? 'display-with-menu' : ''}>
        <Card className="stream-card">
          {canAudio && videoOn && (
            <button className={`stream-mute ${audioOn && !soundBlocked ? 'on' : ''}`} onClick={toggleSound}
              title={audioOn ? 'Mute this page' : 'Unmute this page'} aria-label={audioOn ? 'Mute' : 'Unmute'}>
              {audioOn && !soundBlocked ? '🔊' : '🔇'}
            </button>
          )}
          {!status ? <div className="center"><Spinner /></div>
            : videoOn && canVideo
              ? <DisplaySurface videoKey={videoKey} onStats={setStats} fallback={mjpegFallback}
                  onFallback={() => setMjpegFallback(true)} gameId={session?.gameId} title={session?.title} />
              : <Empty>{canVideo ? 'Video is off.' : 'Video is not available.'}</Empty>}
        </Card>
        {showMenu && canTelnet && <Card title="Ultimate menu"><Suspense fallback={<Spinner />}><RemoteMenu /></Suspense></Card>}
      </div>

      {canControl ? (
        <div className={canKeyboard ? 'play-controls' : ''}>
          {canJoystick && (
            <Card title={viaBridge ? '🕹 Joystick (via joystick bridge)' : 'Joystick'}>
              <Joystick port={port} enabled onPortChange={(p) => api.joystickPort(p)} singleFire={viaBridge}
                ports={viaBridge ? status?.input?.bridge?.ports ?? [2] : [1, 2]} />
              <p className="muted small">Tick "Arrow keys + Space" to play with your PC keyboard, or plug in a gamepad.</p>
            </Card>
          )}
          {canKeyboard && (
            <Card title="Keyboard">
              <Keyboard mode="rest" />
            </Card>
          )}
        </div>
      ) : status?.connected && (
        <p className="muted small">
          Play with your real joystick and keyboard. Double-click the picture (or press F) for TV mode, S for a
          screenshot. <button className="link" onClick={() => navigate('/catalog')}>Find another game</button>
        </p>
      )}

      <CurrentControls gameId={session?.gameId} />

      {visionState?.enabled && (
        <Card title="AI vision mode (experimental)">
          <div className="vision">
            <div className="type-form">
              <input value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={200} aria-label="Vision goal" />
              {vs?.status === 'running'
                ? <button className="btn btn-danger" onClick={() => api.visionStop().then(setVisionState)}>■ Stop</button>
                : <button className="btn btn-primary" disabled={!visionState.plannerSupported || !videoOn || !canJoystick}
                    onClick={() => api.visionStart(goal, port).then(setVisionState).catch((e) => toast(errorMessage(e), 'error'))}>
                    Take over
                  </button>}
            </div>
            {!visionState.plannerSupported && <p className="muted small">The configured AI provider/model can’t be used for vision.</p>}
            {vs && (
              <div className="small">
                Status: <strong>{vs.status}</strong> · {vs.actions} actions {vs.error && <span className="error-text">· {vs.error}</span>}
                <ul className="feed">{vs.log.slice().reverse().map((l: any, i: number) => (
                  <li key={i}>{l.action ? `${l.action.kind} ${l.action.inputs?.join('+') ?? l.action.key ?? ''} — ${l.action.observation}` : `rejected: ${l.rejected}`}</li>
                ))}</ul>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

/** ⏻ Power off (asks first) and ⏻ Power on — on needs a smart plug: the Ultimate can't be woken over the network. */
function PowerButtons({ connected, canOff }: { connected: boolean; canOff: boolean }) {
  const toast = useToast()
  const [plug, setPlug] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.powerState().then((p) => setPlug(p.plug.configured)).catch(() => setPlug(false)) }, [connected])
  const off = async () => {
    if (!window.confirm('Power off the C64 Ultimate? Anything running stops (unsaved progress is lost).'
      + (plug ? '' : '\n\nWithout a smart plug you will need its power switch to turn it back on.'))) return
    setBusy(true)
    try {
      await api.powerOff()
      toast('⏻ C64 Ultimate powered off', 'ok')
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const on = async () => {
    setBusy(true)
    try {
      const r = await api.powerOn()
      toast(`⏻ ${r.note}`, 'ok')
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  if (connected) {
    return canOff ? <button className="btn btn-power" onClick={off} disabled={busy} title="Switch the C64 Ultimate off">⏻ Power off</button> : null
  }
  return (
    <button className="btn btn-power-on" onClick={on} disabled={busy || plug === false}
      title={plug ? 'Switch the C64 Ultimate on through its smart plug' : "The C64 Ultimate can't be switched on over the network — use its power switch, or add a smart plug in Settings → C64 Ultimate"}>
      {busy ? '⏻ Starting…' : '⏻ Power on'}</button>
  )
}
