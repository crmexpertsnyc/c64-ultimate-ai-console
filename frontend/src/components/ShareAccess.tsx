import { createPortal } from 'react-dom'
import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { AccessInfo, AccessUrl } from '../services/api'
import { useToast } from './Toasts'

const KIND_ICON: Record<AccessUrl['kind'], string> = {
  custom: '★', secure: '🔒', lan: '🏠', tailscale: '🌐', hostname: '💻', other: '⋯',
}

/** Copy that also works on plain-http LAN pages, where navigator.clipboard is unavailable. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through */ }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  ta.remove()
  return ok
}

/** QR code + addresses for opening the console on a phone, tablet or another computer. */
export function ShareAccessPanel({ compact = false }: { compact?: boolean }) {
  const toast = useToast()
  const [info, setInfo] = useState<AccessInfo | null>(null)
  const [error, setError] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)

  useEffect(() => { api.access().then(setInfo).catch(() => setError(true)) }, [])

  if (error) return <p className="muted">Could not detect this computer's network addresses.</p>
  if (!info) return <p className="muted">Finding addresses…</p>

  const useful = info.urls.filter((u) => u.kind !== 'other')
  const extra = info.urls.filter((u) => u.kind === 'other')
  const current = picked ?? info.urls.find((u) => u.primary)?.url ?? null
  const copy = async (url: string) => {
    toast((await copyText(url)) ? 'Address copied' : 'Select the address and copy it by hand', 'ok')
  }

  if (!current) {
    return <p className="muted">No network connection found on this computer — connect it to Wi-Fi or Ethernet first.</p>
  }

  return (
    <div className={`share ${compact ? 'share-compact' : ''}`}>
      {info.localOnly && (
        <div className="banner banner-warn small">
          The console is set to <code>WEB_HOST=127.0.0.1</code>, so other devices cannot connect. Set
          <code> WEB_HOST=0.0.0.0</code> in <code>.env</code> and restart it.
        </div>
      )}
      <div className="share-main">
        <img className="share-qr" src={`/api/access/qr.svg?url=${encodeURIComponent(current)}`}
          alt={`QR code for ${current}`} width={180} height={180} />
        <div className="share-text">
          <p className="small"><b>On your iPhone, iPad or Android:</b> open the camera, point it at the code and tap the link.
            Or type this into the browser:</p>
          {info.urls.find((u) => u.url === current)?.kind === 'secure' && (
            <p className="small">🔒 <b>Secure address</b> — works at home <i>and</i> away. The phone needs the free
              <b> Tailscale</b> app, signed in to your account. Unlocks the microphone, gamepads and installing as an app.</p>
          )}
          {info.urls.find((u) => u.url === current)?.kind === 'lan' && (
            <p className="small">🏠 <b>Home address</b> — no app needed, but only works on your home Wi-Fi.</p>
          )}
          <div className="share-url">
            <code className="mono" onClick={(e) => window.getSelection()?.selectAllChildren(e.currentTarget)}>{current}</code>
            <button className="btn btn-sm" onClick={() => copy(current)}>Copy</button>
          </div>
          <p className="muted small">This computer must stay on (the console starts by itself when it boots).
            Tip: install it as an app — iPhone: <b>Share → Add to Home Screen</b>; Android/Chrome: <b>Install app</b>.</p>
        </div>
      </div>

      {useful.length > 1 && (
        <ul className="share-list">
          {useful.map((u) => (
            <li key={u.url}>
              <button className={`share-pick ${u.url === current ? 'on' : ''}`} onClick={() => setPicked(u.url)}
                title="Show QR code for this address">
                <span aria-hidden>{KIND_ICON[u.kind]}</span>
                <span className="mono">{u.url}</span>
                <span className="muted small">{u.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <details className="small">
        <summary>It doesn't open on my phone</summary>
        <ul>
          <li>Check the phone is on your <b>home Wi-Fi</b>, not mobile data or a guest network.</li>
          <li>Type <code>http://</code> — not <code>https://</code> — and include <code>:{info.port}</code>.</li>
          <li>On this PC, Windows must treat the network as <b>Private</b> and allow Python through the firewall
            (Windows asks the first time the console starts — choose Allow).</li>
          <li>The 🎙 microphone and gamepads need the 🔒 secure address; on the 🏠 home address everything else works.</li>
          {useful.some((u) => u.kind === 'tailscale') && (
            <li>🌐 Tailscale addresses work from anywhere, on devices signed in to your Tailscale account.</li>
          )}
          {extra.length > 0 && (
            <li>Other adapters on this PC (usually virtual, not reachable from phones): <span className="mono">{extra.map((u) => u.url).join(', ')}</span></li>
          )}
          <li>Wrong address shown (e.g. in Docker)? Set <code>PUBLIC_URL</code> in <code>.env</code>.</li>
        </ul>
      </details>
    </div>
  )
}

/** Top-bar button that opens the panel in a dialog. */
export function ShareButton() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)} title="Open this console on a phone, tablet or another computer">
        📱 <span className="hide-sm">Open on phone</span>
      </button>
      {/* Rendered into <body>: the top bar's backdrop blur would otherwise pin this "fixed" dialog to the
          bar, pushing it off the top of the screen. */}
      {open && createPortal(
        <div className="modal-backdrop" onClick={() => setOpen(false)}>
          <div className="modal" role="dialog" aria-label="Open on another device" onClick={(e) => e.stopPropagation()}>
            <h2>Open on another device</h2>
            <ShareAccessPanel />
            <div className="modal-foot">
              <button className="btn" onClick={() => setOpen(false)}>Close</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
