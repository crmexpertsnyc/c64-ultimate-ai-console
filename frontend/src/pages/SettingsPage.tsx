import { useEffect, useState } from 'react'
import { UpdatesPanel } from '../components/UpdatesPanel'
import { FirmwareNotice } from '../components/FirmwareNotice'
import { useLocation, useNavigate } from 'react-router-dom'
import { CapabilityTable } from '../components/CapabilityTable'
import { Card, Spinner } from '../components/common'
import { FolderPicker } from '../components/FolderPicker'
import { McpConnect } from '../components/McpConnect'
import { ShareAccessPanel } from '../components/ShareAccess'
import { JoyBridgeCard } from '../components/JoyBridgeCard'
import { PasswordCard } from '../components/SignIn'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { Settings } from '../shared/types'

const STREAM_PRESETS: Record<string, string> = {
  twitch: 'rtmp://live.twitch.tv/app',
  youtube: 'rtmp://a.rtmp.youtube.com/live2',
}

const AI_PRESETS: Record<string, { base: string; hint: string }> = {
  none: { base: '', hint: 'Rules-only parser. Everything works without AI.' },
  vllm: { base: 'http://dgx-spark:8000/v1', hint: 'vLLM OpenAI-compatible server (e.g. on a DGX Spark).' },
  ollama: { base: 'http://localhost:11434', hint: 'Ollama native API.' },
  openwebui: { base: 'http://localhost:3000/api', hint: 'OpenWebUI (API key from Settings → Account).' },
  openai: { base: 'https://api.openai.com/v1', hint: 'Any OpenAI-compatible endpoint.' },
  anthropic: { base: 'https://api.anthropic.com', hint: 'Anthropic Messages API.' },
}

export function SettingsPage() {
  const { status, scan } = useLive()
  const toast = useToast()
  const navigate = useNavigate()
  const [s, setS] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Settings>({})
  const [saving, setSaving] = useState(false)
  const [aiPing, setAiPing] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)

  const location = useLocation()

  useEffect(() => {
    api.settings().then(setS).catch((e) => toast(errorMessage(e), 'error'))
    api.stats().then(setStats).catch(() => {})
  }, [toast, scan?.running])

  // Deep links such as /settings#streaming scroll to that section once the page has loaded.
  useEffect(() => {
    if (!s || !location.hash) return
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [s, location.hash])

  if (!s) return <div className="center"><Spinner /></div>
  const v = (k: string) => (k in draft ? draft[k] : s[k])
  const set = (k: string, val: unknown) => setDraft((d) => ({ ...d, [k]: val }))

  const save = async (extra: Settings = {}) => {
    setSaving(true)
    try {
      const next = await api.saveSettings({ ...draft, ...extra })
      setS(next)
      setDraft({})
      toast('Settings saved', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  const paths: string[] = String(v('LIBRARY_PATHS') || '').split(/[;,]/).map((p) => p.trim()).filter(Boolean)
  const addPath = async (p: string) => {
    const merged = Array.from(new Set([...paths, p]))
    await save({ LIBRARY_PATHS: merged.join(';') })
  }
  const removePath = (p: string) => save({ LIBRARY_PATHS: paths.filter((x) => x !== p).join(';') })
  const rescan = async () => {
    try {
      await api.scan(paths)
      toast('Scan started', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const dirty = Object.keys(draft).length > 0
  const aiDirty = Object.keys(draft).some((k) => k.startsWith('AI_') || k === 'BRAVE_API_KEY' || k === 'ASK_WEB_SEARCH')

  return (
    <div className="page">
      <div className="page-head">
        <h1>Settings</h1>
        <button className="btn btn-ghost" onClick={() => navigate('/setup')}>Run setup wizard</button>
        <button className="btn btn-primary" disabled={!dirty || saving} onClick={() => save()}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>

      <Card title="📱 Open on your phone, tablet or another computer">
        <div id="access" />
        <ShareAccessPanel />
      </Card>

      <div className="grid-2">
        <Card title="C64 Ultimate">
          <FirmwareNotice />
          <div className="form-grid">
            <label className="field"><span>Host / IP</span>
              <input value={v('C64_ULTIMATE_HOST') ?? ''} onChange={(e) => set('C64_ULTIMATE_HOST', e.target.value)} placeholder="192.168.1.64" /></label>
            <label className="field"><span>Port</span>
              <input type="number" value={v('C64_ULTIMATE_PORT')} onChange={(e) => set('C64_ULTIMATE_PORT', Number(e.target.value))} /></label>
            <label className="field"><span>Protocol</span>
              <select value={v('C64_ULTIMATE_PROTOCOL')} onChange={(e) => set('C64_ULTIMATE_PROTOCOL', e.target.value)}>
                <option value="http">http</option><option value="https">https</option></select></label>
            <label className="field"><span>Network password {s.C64_ULTIMATE_PASSWORD_SET ? '(set)' : '(none)'}</span>
              <input type="password" value={draft.C64_ULTIMATE_PASSWORD ?? ''} placeholder={s.C64_ULTIMATE_PASSWORD_SET ? '•••••• (unchanged)' : ''}
                onChange={(e) => set('C64_ULTIMATE_PASSWORD', e.target.value)} autoComplete="new-password" /></label>
            <label className="field"><span>Typing delay (ms per key)</span>
              <input type="number" value={v('C64_TYPE_DELAY_MS')} onChange={(e) => set('C64_TYPE_DELAY_MS', Number(e.target.value))} /></label>
            <label className="field"><span>Upload encoding</span>
              <select value={v('C64_UPLOAD_MODE')} onChange={(e) => set('C64_UPLOAD_MODE', e.target.value)}>
                <option value="raw">raw body (default)</option><option value="multipart">multipart form</option></select></label>
            <label className="field field-check"><span>Simulation mode (SIMULATE_C64)</span>
              <input type="checkbox" checked={!!v('SIMULATE_C64')} onChange={(e) => set('SIMULATE_C64', e.target.checked)} /></label>
            <label className="field"><span>Simulated firmware</span>
              <select value={v('SIMULATE_PROFILE')} onChange={(e) => set('SIMULATE_PROFILE', e.target.value)}>
                <option value="modern">modern (REST input + menu screen)</option>
                <option value="legacy">legacy (Commodore 1.1.0 / Spiffy)</option></select></label>
          </div>
          <div className="row-actions">
            {s.C64_ULTIMATE_PASSWORD_SET && <button className="btn btn-ghost" onClick={() => save({ CLEAR_C64_ULTIMATE_PASSWORD: true })}>Clear password</button>}
            <button className="btn" onClick={() => api.reconnect().then(() => toast('Re-probed', 'ok')).catch((e) => toast(errorMessage(e), 'error'))}>Reconnect & re-probe</button>
          </div>
        </Card>

        <Card title="AI assistant (optional)">
          <p className="muted small">{AI_PRESETS[v('AI_PROVIDER')]?.hint}</p>
          <div className="form-grid">
            <label className="field"><span>Provider</span>
              <select value={v('AI_PROVIDER')} onChange={(e) => {
                set('AI_PROVIDER', e.target.value)
                if (!v('AI_BASE_URL')) set('AI_BASE_URL', AI_PRESETS[e.target.value]?.base ?? '')
              }}>
                {Object.keys(AI_PRESETS).map((p) => <option key={p} value={p}>{p}</option>)}
              </select></label>
            <label className="field"><span>Model</span>
              <input value={v('AI_MODEL') ?? ''} onChange={(e) => set('AI_MODEL', e.target.value)} placeholder="e.g. llama3.1:8b" /></label>
            <label className="field field-wide"><span>Base URL</span>
              <input value={v('AI_BASE_URL') ?? ''} onChange={(e) => set('AI_BASE_URL', e.target.value)} placeholder={AI_PRESETS[v('AI_PROVIDER')]?.base} /></label>
            <label className="field field-wide"><span>API key {s.AI_API_KEY_SET ? '(set)' : '(none)'}</span>
              <input type="password" value={draft.AI_API_KEY ?? ''} placeholder={s.AI_API_KEY_SET ? '•••••• (unchanged)' : 'not needed for most local servers'}
                onChange={(e) => set('AI_API_KEY', e.target.value)} autoComplete="new-password" /></label>
            <label className="field field-wide"><span>🔎 Brave Search API key for 💬 Ask {s.BRAVE_API_KEY_SET ? '(set)' : '(none)'}</span>
              <input type="password" value={draft.BRAVE_API_KEY ?? ''} placeholder={s.BRAVE_API_KEY_SET ? '•••••• (unchanged)' : 'optional — lets Ask research the web (brave.com/search/api)'}
                onChange={(e) => set('BRAVE_API_KEY', e.target.value)} autoComplete="new-password" /></label>
            <label className="toggle"><input type="checkbox" checked={v('ASK_WEB_SEARCH') !== false}
              onChange={(e) => set('ASK_WEB_SEARCH', e.target.checked)} /> Use web search when answering questions</label>
          </div>
          <div className="row-actions">
            <button className="btn" onClick={async () => {
              try {
                const unsaved = Object.fromEntries(Object.entries(draft).filter(([k]) => k.startsWith('AI_')))
                setAiPing(await api.aiTest(unsaved))
              } catch (e) { toast(errorMessage(e), 'error') }
            }}>Test provider</button>
            <button className="btn" onClick={async () => {
              try {
                const r = await api.testSearch(draft.BRAVE_API_KEY || undefined)
                toast(r.ok ? `🔎 Brave Search works (${r.results} results, e.g. “${r.sample}”)` : `Brave Search: ${r.error}`, r.ok ? 'ok' : 'error')
              } catch (e) { toast(errorMessage(e), 'error') }
            }} title="Checks the Brave key (the typed one, or the saved one)">Test search</button>
            <button className="btn btn-primary" disabled={!aiDirty || saving} onClick={() => save()}>Save AI settings</button>
          </div>
          {aiPing && (
            <div className={`small ${aiPing.ping?.ok ? '' : 'error-text'}`}>
              {aiPing.ping?.ok
                ? `Reachable. ${aiPing.ping.models?.length ? `Models: ${aiPing.ping.models.join(', ')}. Configured model ${aiPing.ping.modelAvailable === false ? 'NOT found' : 'found'}.` : ''}`
                : `Not reachable: ${aiPing.ping?.detail}${aiPing.ping?.detail === 'HTTP 401' ? ' — the server needs an API key' : ''}`}
              {aiPing.unsaved && <strong> Tested unsaved values — click “Save AI settings” to keep them.</strong>}
            </div>
          )}
        </Card>
      </div>

      <Card title="🛒 Hardware shop & eBay (optional)">
        <p className="muted small">The Hardware page uses a built-in catalog that links to the sellers. To publish your own catalog (with your affiliate
          links) from your website, enter its https address. eBay keys turn on live used-gear listings and 💰 price alerts; an eBay Partner Network
          campaign id tags eBay links as affiliate links.</p>
        <div className="form-grid">
          <label className="field field-wide"><span>Catalog URL (JSON from your website)</span>
            <input value={v('SHOP_CATALOG_URL') ?? ''} onChange={(e) => set('SHOP_CATALOG_URL', e.target.value)} placeholder="https://… — empty = built-in catalog" /></label>
          <label className="field"><span>eBay app ID (Client ID)</span>
            <input value={v('EBAY_CLIENT_ID') ?? ''} onChange={(e) => set('EBAY_CLIENT_ID', e.target.value)} autoComplete="off" /></label>
          <label className="field"><span>eBay Cert ID (Client secret) {s.EBAY_CLIENT_SECRET_SET ? '(set)' : '(none)'}</span>
            <input type="password" value={draft.EBAY_CLIENT_SECRET ?? ''} placeholder={s.EBAY_CLIENT_SECRET_SET ? '•••••• (unchanged)' : 'from developer.ebay.com'}
              onChange={(e) => set('EBAY_CLIENT_SECRET', e.target.value)} autoComplete="new-password" /></label>
          <label className="field"><span>eBay Partner Network campaign id</span>
            <input value={v('EBAY_CAMPAIGN_ID') ?? ''} onChange={(e) => set('EBAY_CAMPAIGN_ID', e.target.value.replace(/\D/g, ''))} placeholder="10 digits — optional" /></label>
          <label className="field"><span>eBay site</span>
            <select value={v('EBAY_MARKETPLACE') ?? 'EBAY_US'} onChange={(e) => set('EBAY_MARKETPLACE', e.target.value)}>
              {['EBAY_US', 'EBAY_GB', 'EBAY_DE', 'EBAY_FR', 'EBAY_IT', 'EBAY_ES', 'EBAY_CA', 'EBAY_AU'].map((m) => <option key={m} value={m}>{m.replace('EBAY_', 'eBay ')}</option>)}
            </select></label>
          <label className="toggle"><input type="checkbox" checked={v('NEWS_MONITOR') !== false}
            onChange={(e) => set('NEWS_MONITOR', e.target.checked)} /> Check news, releases, events, firmware and price alerts in the background</label>
        </div>
      </Card>

      <Card title="🔄 Sources & updates" className="anchor">
        <div id="updates" />
        <p className="muted small">The console keeps news, new releases, events, magazines, hardware and firmware news up to date by itself, one source at a time.
          Change how often each is checked, switch any off, or check one now.</p>
        <label className="field"><span>⭐ Events: highlight and list first</span>
          <input value={v('EVENTS_HOME_COUNTRY') ?? 'United States'} onChange={(e) => set('EVENTS_HOME_COUNTRY', e.target.value)} placeholder="United States" /></label>
        <UpdatesPanel />
      </Card>

      <Card title="Game library" className="anchor" actions={
        <button className="btn btn-primary" disabled={!paths.length || scan?.running} onClick={rescan}>{scan?.running ? 'Scanning…' : 'Scan now'}</button>
      }>
        <div id="library" />
        {stats && <p className="muted small">{stats.games} titles · {stats.media} files · {stats.favorites} favorites. Source files are only read — never renamed, moved or deleted.</p>}
        <ul className="path-list">
          {paths.map((p) => {
            const root = stats?.roots?.find((r: any) => r.path === p)
            return (
              <li key={p}>
                <span className="mono">{p}</span>
                {root && <span className="muted small"> · {root.fileCount} files{root.lastScan ? ` · scanned ${new Date(root.lastScan).toLocaleString()}` : ''}</span>}
                {root?.lastError && <span className="error-text small"> · {root.lastError}</span>}
                <button className="btn btn-ghost btn-sm" onClick={() => removePath(p)}>Remove</button>
              </li>
            )
          })}
          {!paths.length && <li className="muted">No folders yet.</li>}
        </ul>
        {scan?.results?.length > 0 && !scan.running && (
          <p className="small">Last scan: {scan.results.map((r: any) => `${r.root}: ${r.files_found} files, ${r.games_created} new titles, ${r.missing} missing`).join(' · ')}</p>
        )}
        <FolderPicker onPick={addPath} />
      </Card>

      <PasswordCard />

      <JoyBridgeCard />

      <McpConnect />

      <Card title="Streaming & screenshots">
        <div id="streaming" />
        <div className="form-grid">
          <label className="field"><span>Service</span>
            <select value={Object.entries(STREAM_PRESETS).find(([, u]) => u === v('LIVE_RTMP_URL'))?.[0] ?? 'custom'}
              onChange={(e) => { if (e.target.value !== 'custom') set('LIVE_RTMP_URL', STREAM_PRESETS[e.target.value]) }}>
              <option value="twitch">Twitch</option>
              <option value="youtube">YouTube</option>
              <option value="custom">Custom RTMP server</option>
            </select></label>
          <label className="field field-wide"><span>RTMP server</span>
            <input value={v('LIVE_RTMP_URL') ?? ''} onChange={(e) => set('LIVE_RTMP_URL', e.target.value)}
              placeholder="rtmp://live.twitch.tv/app" /></label>
          <label className="field field-wide"><span>Stream key {s.LIVE_STREAM_KEY_SET ? '(set)' : '(none)'}</span>
            <input type="password" value={draft.LIVE_STREAM_KEY ?? ''} autoComplete="new-password"
              placeholder={s.LIVE_STREAM_KEY_SET ? '•••••• (unchanged)' : 'from your Twitch/YouTube dashboard'}
              onChange={(e) => set('LIVE_STREAM_KEY', e.target.value)} /></label>
          <label className="field"><span>Video bitrate (kbps)</span>
            <input type="number" min={500} max={8000} value={v('LIVE_VIDEO_BITRATE_KBPS')}
              onChange={(e) => set('LIVE_VIDEO_BITRATE_KBPS', Number(e.target.value))} /></label>
          <label className="field field-check"><span>Automatic cover art (grab a frame after a game starts)</span>
            <input type="checkbox" checked={!!v('AUTO_COVER_ART')} onChange={(e) => set('AUTO_COVER_ART', e.target.checked)} /></label>
        </div>
        <p className="muted small">
          For OBS instead: add a <strong>Browser Source</strong> with URL <code>{window.location.origin}/stream-view</code> (1280×720,
          “Control audio via OBS”). Screenshots and recordings are in the Gallery.
        </p>
        <div className="row-actions">
          <button className="btn btn-primary" disabled={!dirty || saving} onClick={() => save()}>Save</button>
        </div>
      </Card>

      <details className="advanced">
        <summary>Advanced</summary>
        <div className="row-actions">
          <button className="btn" onClick={() => navigate('/troubleshooting')}>≡ Logs & troubleshooting</button>
          <button className="btn" onClick={() => navigate('/assembly64')}>⇅ Assembly64 servers</button>
          <a className="btn" href="/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
        {status?.capabilities && (
          <Card title={`Capabilities — ${status.capabilities.product || 'device'} ${status.capabilities.firmware}`}>
            <CapabilityTable caps={status.capabilities} />
          </Card>
        )}
      </details>
    </div>
  )
}
