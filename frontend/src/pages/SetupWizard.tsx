import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CapabilityTable } from '../components/CapabilityTable'
import { Spinner, Stat } from '../components/common'
import { FolderPicker } from '../components/FolderPicker'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'

const STEPS = ['Welcome', 'Device', 'Password', 'Test', 'Library', 'Scan', 'Done']

export function SetupWizard() {
  const navigate = useNavigate()
  const { status, scan } = useLive()
  const [step, setStep] = useState(0)
  const [host, setHost] = useState('')
  const [port, setPort] = useState(80)
  const [protocol, setProtocol] = useState<'http' | 'https'>('http')
  const [hasPassword, setHasPassword] = useState<boolean | null>(null)
  const [password, setPassword] = useState('')
  const [test, setTest] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [paths, setPaths] = useState<string[]>([])
  const [simulated, setSimulated] = useState(false)

  const runTest = async () => {
    setBusy(true)
    setError(null)
    setTest(null)
    try {
      const r = await api.testConnection(host.trim(), port, hasPassword ? password : '', protocol)
      setTest(r)
      if (!r.ok) setError(r.authRequired ? 'The Ultimate rejected the password (HTTP 403).' : r.error ?? 'Connection failed')
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const saveDevice = async () => {
    setBusy(true)
    try {
      await api.saveSettings({
        C64_ULTIMATE_HOST: host.trim(), C64_ULTIMATE_PORT: port, C64_ULTIMATE_PROTOCOL: protocol,
        SIMULATE_C64: false, ...(hasPassword ? { C64_ULTIMATE_PASSWORD: password } : { CLEAR_C64_ULTIMATE_PASSWORD: true }),
      })
      setStep(4)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const useSimulator = async () => {
    setBusy(true)
    try {
      await api.saveSettings({ SIMULATE_C64: true })
      setSimulated(true)
      setStep(3)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const startScan = async () => {
    setError(null)
    try {
      await api.scan(paths)
      setStep(5)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const finish = async () => {
    await api.saveSettings({ SETUP_COMPLETE: true })
    navigate('/', { replace: true })
    location.reload()
  }

  const caps = simulated ? status?.capabilities : test?.capabilities
  const info = simulated ? status?.info : test?.info

  return (
    <div className="wizard">
      <div className="wizard-card">
        <div className="brand wizard-brand">
          <div className="brand-stripes"><i /><i /><i /><i /></div>
          <div><div className="brand-name">C64 Ultimate AI Console</div><div className="brand-sub">Setup</div></div>
        </div>
        <ol className="wizard-steps">
          {STEPS.map((s, i) => <li key={s} className={i === step ? 'on' : i < step ? 'done' : ''}>{s}</li>)}
        </ol>

        {step === 0 && (
          <div className="wizard-body">
            <h2>Welcome</h2>
            <p>This console controls your Commodore 64 Ultimate over its documented network API. It never touches
              firmware, FPGA images or your files.</p>
            <div className="row-actions big">
              <button className="btn btn-primary btn-lg" onClick={() => setStep(1)}>Connect my C64 Ultimate</button>
              <button className="btn btn-lg" onClick={useSimulator} disabled={busy}>Try simulation mode</button>
            </div>
          </div>
        )}

        {step === 1 && (
          <form className="wizard-body" onSubmit={(e) => { e.preventDefault(); setStep(2) }}>
            <h2>Where is your C64 Ultimate?</h2>
            <p className="muted">Find the IP address in the Ultimate menu under network settings. Make sure the web/REST
              service is enabled.</p>
            <div className="form-grid">
              <label className="field field-wide"><span>IP address or hostname</span>
                <input autoFocus value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.64" required /></label>
              <label className="field"><span>Port</span>
                <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} min={1} max={65535} /></label>
              <label className="field"><span>Protocol</span>
                <select value={protocol} onChange={(e) => setProtocol(e.target.value as 'http' | 'https')}>
                  <option value="http">http</option><option value="https">https</option></select></label>
            </div>
            <div className="row-actions">
              <button type="button" className="btn" onClick={() => setStep(0)}>Back</button>
              <button className="btn btn-primary" disabled={!host.trim()}>Next</button>
            </div>
          </form>
        )}

        {step === 2 && (
          <div className="wizard-body">
            <h2>Is a network password configured on the Ultimate?</h2>
            <div className="row-actions">
              <button className={`btn ${hasPassword === false ? 'btn-primary' : ''}`} onClick={() => setHasPassword(false)}>No</button>
              <button className={`btn ${hasPassword ? 'btn-primary' : ''}`} onClick={() => setHasPassword(true)}>Yes</button>
            </div>
            {hasPassword && (
              <label className="field"><span>Network password (sent as the X-Password header, never logged)</span>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" /></label>
            )}
            <div className="row-actions">
              <button className="btn" onClick={() => setStep(1)}>Back</button>
              <button className="btn btn-primary" disabled={hasPassword === null || (hasPassword && !password)}
                onClick={() => { setStep(3); runTest() }}>Test connection</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <h2>{simulated ? 'Simulated C64 Ultimate' : 'Connection test'}</h2>
            {busy && <p><Spinner /> Probing {host}… (read-only checks only)</p>}
            {error && <div className="banner banner-bad">{error}</div>}
            {info && (
              <div className="stats">
                <Stat label="Product" value={info.product} />
                <Stat label="Firmware" value={info.firmwareVersion} />
                <Stat label="FPGA" value={info.fpgaVersion} />
                <Stat label="Core" value={info.coreVersion} />
                <Stat label="Hostname" value={info.hostname} />
                <Stat label="REST API" value={simulated ? status?.apiVersion : test?.apiVersion} />
              </div>
            )}
            {caps && <CapabilityTable caps={caps} compact />}
            <div className="row-actions">
              {!simulated && <button className="btn" onClick={() => setStep(error?.includes('403') ? 2 : 1)}>Back</button>}
              {!simulated && <button className="btn" onClick={runTest} disabled={busy}>Retry</button>}
              {simulated
                ? <button className="btn btn-primary" onClick={() => setStep(4)}>Next</button>
                : <button className="btn btn-primary" disabled={!test?.ok || busy} onClick={saveDevice}>Save & continue</button>}
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="wizard-body">
            <h2>Where are your games?</h2>
            <p className="muted">Pick folders on this computer or a mounted network share (e.g. /Games, /C64, /Assembly64).
              They are scanned recursively and only read.</p>
            <ul className="path-list">
              {paths.map((p) => <li key={p}><span className="mono">{p}</span>
                <button className="btn btn-ghost btn-sm" onClick={() => setPaths(paths.filter((x) => x !== p))}>Remove</button></li>)}
            </ul>
            <FolderPicker onPick={(p) => setPaths((ps) => Array.from(new Set([...ps, p])))} />
            {error && <div className="banner banner-bad">{error}</div>}
            <div className="row-actions">
              <button className="btn" onClick={() => setStep(6)}>Skip for now</button>
              <button className="btn btn-primary" disabled={!paths.length} onClick={startScan}>Scan games</button>
            </div>
          </div>
        )}

        {step === 5 && (
          <div className="wizard-body">
            <h2>Scanning…</h2>
            {scan?.running !== false ? <p><Spinner /> Looking for D64, D71, D81, G64, G71, PRG, CRT, T64, SID and MOD files…</p> : (
              <>
                {scan.error && <div className="banner banner-bad">{scan.error}</div>}
                <ul>{scan.results.map((r: any) => (
                  <li key={r.root}><span className="mono">{r.root}</span>: {r.files_found} files, {r.games_created} titles
                    {r.errors.length > 0 && <span className="error-text"> · {r.errors[0]}</span>}</li>
                ))}</ul>
              </>
            )}
            <div className="row-actions">
              <button className="btn btn-primary" disabled={scan?.running !== false} onClick={() => setStep(6)}>Next</button>
            </div>
          </div>
        )}

        {step === 6 && (
          <div className="wizard-body">
            <h2>Ready</h2>
            <p>Try saying or typing “Play Bruce Lee”, “Put disk 2 in” or “Open the Ultimate menu”.</p>
            <button className="btn btn-primary btn-lg" onClick={finish}>Open the console</button>
          </div>
        )}
      </div>
    </div>
  )
}
