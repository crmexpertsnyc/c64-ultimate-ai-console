import { Fragment, useCallback, useEffect, useState } from 'react'
import { Card, Empty } from '../components/common'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { AuditEntry } from '../shared/types'

export function Troubleshooting() {
  const { audit: live } = useLive()
  const toast = useToast()
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [diag, setDiag] = useState<any>(null)
  const [failures, setFailures] = useState(false)
  const [source, setSource] = useState('')
  const [open, setOpen] = useState<number | null>(null)

  const load = useCallback(() => {
    api.audit({ limit: 200, failures, source: source || undefined }).then(setEntries).catch((e) => toast(errorMessage(e), 'error'))
    api.troubleshooting().then(setDiag).catch(() => {})
  }, [failures, source, toast])

  useEffect(() => { load() }, [load, live.length])

  const download = () => {
    const blob = new Blob([JSON.stringify({ diagnostics: diag, audit: entries }, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `c64-console-diagnostics-${new Date().toISOString().slice(0, 19)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Logs & troubleshooting</h1>
        <button className="btn" onClick={load}>Refresh</button>
        <button className="btn btn-ghost" onClick={download} disabled={!diag}>Download diagnostics</button>
      </div>

      <Card title="🐞 Browser Play problems">
        <p className="muted small">Games that did not work in the browser emulator — detected automatically or reported with ⚑ Report — with diagnostics for investigating.</p>
        <a className="btn" href="/compatibility">Open the compatibility log</a>
      </Card>

      {diag?.hints?.length > 0 && (
        <Card title="Hints">
          <ul className="hints">{diag.hints.map((h: string, i: number) => <li key={i}>{h}</li>)}</ul>
        </Card>
      )}

      <div className="grid-2">
        <Card title="Connection">
          {diag ? (
            <dl className="kv">
              <dt>Base URL</dt><dd className="mono">{diag.device.baseUrl}</dd>
              <dt>Connected</dt><dd>{String(diag.device.connected)}</dd>
              <dt>Last error</dt><dd>{diag.device.lastError ?? '—'}</dd>
              <dt>Last contact</dt><dd>{diag.device.lastContact ? new Date(diag.device.lastContact * 1000).toLocaleTimeString() : '—'}</dd>
              <dt>REST API</dt><dd>{diag.device.apiVersion || '—'}</dd>
              <dt>Input mode</dt><dd>{diag.device.inputMode}</dd>
              <dt>App</dt><dd>{diag.app.version} · Python {diag.app.python}</dd>
              <dt>AI</dt><dd>{diag.ai.provider}{diag.ai.model ? ` · ${diag.ai.model}` : ''}</dd>
            </dl>
          ) : <Empty>Loading…</Empty>}
        </Card>
        <Card title="Recent REST calls to the Ultimate">
          <div className="calls">
            {(diag?.recentCalls ?? []).slice().reverse().slice(0, 40).map((c: any, i: number) => (
              <div key={i} className={`call ${c.ok ? '' : 'call-bad'}`}>
                <span className="mono">{c.method}</span> <span className="mono">{c.path}</span>
                <span className="muted"> → {c.status || 'ERR'} · {Math.round(c.elapsed_ms)} ms</span>
                {c.errors?.length > 0 && <span className="error-text small"> {c.errors.join('; ')}</span>}
              </div>
            ))}
            {!diag?.recentCalls?.length && <Empty>No calls yet.</Empty>}
          </div>
        </Card>
      </div>

      <Card title="Audit log" actions={
        <>
          <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
            <option value="">All sources</option>
            {['ui', 'command', 'api', 'mcp', 'vision', 'system'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="toggle"><input type="checkbox" checked={failures} onChange={(e) => setFailures(e.target.checked)} /> Failures only</label>
        </>
      }>
        <div className="table-wrap">
          <table className="audit">
            <thead><tr><th>Time</th><th>Source</th><th>Command / operation</th><th>Intent</th><th>Result</th><th>ms</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <Fragment key={e.id}>
                  <tr className={e.success ? '' : 'row-bad'} onClick={() => setOpen(open === e.id ? null : e.id)}>
                    <td className="mono small">{new Date(e.timestamp).toLocaleTimeString()}</td>
                    <td><span className={`feed-src src-${e.source}`}>{e.source}</span></td>
                    <td>{e.userCommand ? <><div>“{e.userCommand}”</div><div className="muted small">{e.operation}</div></> : e.operation}</td>
                    <td className="small mono">{e.intent?.intent ?? ''}</td>
                    <td>{e.success ? '✓' : <span className="error-text">✕ {e.error}</span>}</td>
                    <td className="mono small">{Math.round(e.durationMs)}</td>
                  </tr>
                  {open === e.id && (
                    <tr className="row-detail"><td colSpan={6}>
                      <div className="small"><strong>API calls</strong></div>
                      {e.apiCalls.length ? e.apiCalls.map((c, i) => (
                        <div key={i} className="mono small">{c.method} {c.path} → {c.status} ({Math.round(c.elapsed_ms)} ms)</div>
                      )) : <div className="muted small">none</div>}
                      {e.intent && <pre className="json">{JSON.stringify(e.intent, null, 2)}</pre>}
                      {e.deviceResponse && <pre className="json">{e.deviceResponse}</pre>}
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          {!entries.length && <Empty>No entries.</Empty>}
        </div>
      </Card>
    </div>
  )
}
