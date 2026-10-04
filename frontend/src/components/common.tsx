import { useEffect } from 'react'
import type { ReactNode } from 'react'
import type { AuditEntry, CapState, LaunchJob } from '../shared/types'

export function Card({ title, actions, children, className = '' }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value ?? '—'}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

export function Dot({ tone }: { tone: 'ok' | 'warn' | 'bad' | 'idle' }) {
  return <span className={`dot dot-${tone}`} aria-hidden />
}

const CAP_TEXT: Record<CapState, string> = {
  supported: 'Supported', unsupported: 'Unsupported', unverified: 'Unverified', unknown: 'Unknown',
}

export function CapBadge({ state }: { state: CapState }) {
  return <span className={`badge badge-${state}`}>{CAP_TEXT[state]}</span>
}

export function Modal({ open, title, onClose, children, footer }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal aria-label={title} onClick={(e) => e.stopPropagation()}>
        <header className="card-head"><h2>{title}</h2><button className="btn btn-ghost" onClick={onClose} aria-label="Close">✕</button></header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function LaunchProgress({ job }: { job: LaunchJob | null }) {
  if (!job) return null
  const icon = { pending: '·', running: '▶', ok: '✓', skipped: '–', failed: '✕' }
  return (
    <div className={`launch launch-${job.status}`}>
      <div className="launch-head">
        <strong>{job.title}</strong>
        <span className="muted"> · {job.method.replace(/_/g, ' ')}</span>
        <span className={`badge badge-${job.status === 'done' ? 'supported' : job.status === 'failed' ? 'unsupported' : 'unverified'}`}>
          {job.status}
        </span>
      </div>
      <ol className="steps">
        {job.steps.map((s, i) => (
          <li key={i} className={`step step-${s.status}`}>
            <span className="step-icon">{icon[s.status]}</span>
            <span>{s.name}</span>
            {s.detail && <span className="muted"> — {s.detail}</span>}
          </li>
        ))}
      </ol>
      {job.error && <div className="error-text">{job.error}</div>}
    </div>
  )
}

export function ActivityFeed({ entries, limit = 12 }: { entries: AuditEntry[]; limit?: number }) {
  if (!entries.length) return <Empty>No actions yet.</Empty>
  return (
    <ul className="feed">
      {entries.slice(0, limit).map((e) => (
        <li key={e.id} className={e.success ? '' : 'feed-fail'}>
          <span className={`feed-src src-${e.source}`}>{e.source}</span>
          <span className="feed-op">{e.userCommand ? `“${e.userCommand}”` : e.operation}</span>
          <span className="muted feed-meta">
            {e.userCommand ? e.operation + ' · ' : ''}{Math.round(e.durationMs)} ms
          </span>
          {e.error && <div className="error-text small">{e.error}</div>}
        </li>
      ))}
    </ul>
  )
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
