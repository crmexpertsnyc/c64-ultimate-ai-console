import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { AuthStatus } from '../services/api'
import { Modal } from './common'
import { useToast } from './Toasts'

/**
 * 🔐 Safer remote access by default. While no console password is set:
 * - on another device (phone, laptop, over Tailscale): a prompt with a set-password form, shown again after a week
 *   if dismissed, plus a slim banner;
 * - on the console's own computer: a banner once other devices have used it unprotected.
 * Nothing shows once a password is set.
 */
const SNOOZE_KEY = 'c64.passwordNudge.snoozedUntil'
const WEEK = 7 * 86400 * 1000

function snoozed(): boolean {
  try { return Number(localStorage.getItem(SNOOZE_KEY) || 0) > Date.now() } catch { return false }
}
function snooze(ms: number): void {
  try { localStorage.setItem(SNOOZE_KEY, String(Date.now() + ms)) } catch { /* private window */ }
}

export function PasswordNudge() {
  const toast = useToast()
  const [st, setSt] = useState<AuthStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.authStatus().then((s) => {
      setSt(s)
      if (!s.enabled && !s.local && !snoozed()) setOpen(true)
    }).catch(() => {})
  }, [])

  if (!st || st.enabled || hidden) return null
  if (st.local && !st.remoteDevices) return null          // only this computer uses it: nothing to warn about

  const later = () => { snooze(WEEK); setOpen(false) }
  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (pw.length < 6) { toast('Use at least 6 characters', 'error'); return }
    if (pw !== pw2) { toast("The two passwords don't match", 'error'); return }
    setBusy(true)
    try {
      await api.setPassword('', pw)
      toast('Password set — this device stays signed in; other devices sign in once', 'ok')
      setOpen(false); setHidden(true)
    } catch (err) { toast(errorMessage(err), 'error') } finally { setBusy(false) }
  }

  return (
    <>
      <div className="pw-banner" role="status">
        <span>🔓 {st.local
          ? <>{st.remoteDevices} other device{st.remoteDevices === 1 ? '' : 's'} used this console in the last week without a password.</>
          : <>Anyone on your network can control this C64 — the console has no password.</>}</span>
        {st.local
          ? <Link className="btn btn-sm btn-primary" to="/settings#password">Set a password</Link>
          : <button className="btn btn-sm btn-primary" onClick={() => setOpen(true)}>Set a password</button>}
        <button className="btn btn-ghost btn-sm" onClick={() => setHidden(true)} aria-label="Hide for now">✕</button>
      </div>
      <Modal open={open} title="🔐 Protect your console" onClose={later}>
        <form className="pw-form" onSubmit={save}>
          <p>This console has no password, so <b>anyone who can reach it</b> — on your Wi-Fi, or through your remote access — can
            control your C64, change settings and see your library.</p>
          <p className="muted small">Set one now: this device stays signed in, every other device signs in once (for 30 days). The
            console's own computer never needs it.</p>
          <label className="field"><span>New password</span>
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" minLength={6} autoFocus /></label>
          <label className="field"><span>Same password again</span>
            <input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" minLength={6} /></label>
          <div className="row-actions">
            <button className="btn btn-primary" disabled={busy || !pw || !pw2}>{busy ? 'Saving…' : 'Set password'}</button>
            <button type="button" className="btn btn-ghost" onClick={later}>Remind me in a week</button>
          </div>
        </form>
      </Modal>
    </>
  )
}
