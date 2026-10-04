import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, errorMessage } from '../services/api'
import type { AuthStatus } from '../services/api'
import { Card } from './common'
import { useToast } from './Toasts'

export const AUTH_EVENT = 'c64-auth-required'

/**
 * Shows the sign-in screen instead of the app when a console password is set and this device is not
 * signed in (this computer itself never needs it). Also takes over if a session expires mid-use.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'open' | 'signin'>('checking')

  useEffect(() => {
    api.authStatus()
      .then((s) => setState(s.enabled && !s.signedIn ? 'signin' : 'open'))
      .catch(() => setState('open')) // console unreachable: let the app show its own connection errors
    const onRequired = () => setState('signin')
    window.addEventListener(AUTH_EVENT, onRequired)
    return () => window.removeEventListener(AUTH_EVENT, onRequired)
  }, [])

  if (state === 'checking') return null
  if (state === 'signin') return <SignInPage />
  return <>{children}</>
}

function SignInPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(password)
      window.location.reload() // reconnect live updates with the new session
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={submit}>
        <div className="brand-stripes"><i /><i /><i /><i /></div>
        <h1>C64 Ultimate AI Console</h1>
        <p className="muted">This console is password protected. Sign in once on this device.</p>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password"
          autoFocus autoComplete="current-password" aria-label="Password" />
        {error && <p className="error-text">{error}</p>}
        <button className="btn btn-primary" disabled={!password || busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted small">You stay signed in for 30 days on this device.</p>
      </form>
    </div>
  )
}

/** Settings card: set, change or remove the console password; sign out this device. */
export function PasswordCard() {
  const toast = useToast()
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')

  const load = () => api.authStatus().then(setStatus).catch(() => {})
  useEffect(() => { load() }, [])

  const save = async (value: string) => {
    if (value && value !== confirm) { toast('The two new passwords do not match', 'error'); return }
    try {
      await api.setPassword(current, value)
      setCurrent(''); setNext(''); setConfirm('')
      await load()
      toast(value ? '🔐 Password set — other devices must sign in' : 'Password removed — no sign-in needed', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  if (!status) return null
  const needCurrent = status.enabled && !status.local
  return (
    <Card title="🔐 Password">
      <div id="password" />
      <p className="muted small">
        {status.enabled
          ? 'On: phones, tablets and other computers must sign in once (30 days per device). This computer never needs it.'
          : 'Off: anyone who can reach the console can use it. Tailscale keeps it private — set a password before sharing it more widely.'}
      </p>
      <div className="form-grid">
        {needCurrent && (
          <label className="field"><span>Current password</span>
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></label>
        )}
        <label className="field"><span>{status.enabled ? 'New password' : 'Password'}</span>
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" placeholder="at least 6 characters" /></label>
        <label className="field"><span>Repeat it</span>
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" /></label>
      </div>
      <div className="row-actions">
        <button className="btn btn-primary" disabled={next.length < 6 || (needCurrent && !current)} onClick={() => save(next)}>
          {status.enabled ? 'Change password' : 'Set password'}</button>
        {status.enabled && (
          <button className="btn btn-ghost" disabled={needCurrent && !current} onClick={() => save('')}
            title="Turn sign-in off again">Remove password</button>
        )}
        {status.enabled && !status.local && (
          <button className="btn btn-ghost" onClick={() => api.logout().then(() => window.location.reload())}>Sign out this device</button>
        )}
      </div>
    </Card>
  )
}
