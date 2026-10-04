import { useState } from 'react'
import { Card, Empty } from '../components/common'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'

export function Assembly64Page() {
  const toast = useToast()
  const [current, setCurrent] = useState<any>(null)
  const [gen, setGen] = useState({ name: 'Home Assembly', host: '', port: 8000, client_id: 'Spiffy' })
  const [generated, setGenerated] = useState<any>(null)
  const [server, setServer] = useState('http://localhost:8000')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[] | null>(null)
  const [entries, setEntries] = useState<Record<string, any[]>>({})

  const run = async <T,>(fn: () => Promise<T>, then: (r: T) => void) => {
    try { then(await fn()) } catch (e) { toast(errorMessage(e), 'error') }
  }

  return (
    <div className="page">
      <div className="page-head"><h1>Assembly64 / Home Assembly</h1></div>
      <div className="banner banner-info">
        This app never writes <code>/flash/config/server.json</code>. It reads it (FTP, read-only), generates a suggested
        entry for your local server, and tells you how to install it yourself.
      </div>

      <div className="grid-2">
        <Card title="server.json on the Ultimate" actions={<button className="btn" onClick={() => run(api.serverJson, setCurrent)}>Read</button>}>
          {current ? (current.ok ? <pre className="json">{current.raw}</pre> : <div className="error-text">{current.error}</div>)
            : <Empty>Press Read to fetch it over FTP.</Empty>}
        </Card>
        <Card title="Generate an entry for a local server">
          <div className="form-grid">
            <label className="field"><span>Name</span><input value={gen.name} onChange={(e) => setGen({ ...gen, name: e.target.value })} /></label>
            <label className="field"><span>Host / IP of this server</span><input value={gen.host} onChange={(e) => setGen({ ...gen, host: e.target.value })} placeholder="192.168.1.20" /></label>
            <label className="field"><span>Port</span><input type="number" value={gen.port} onChange={(e) => setGen({ ...gen, port: Number(e.target.value) })} /></label>
            <label className="field"><span>Client-Id</span><input value={gen.client_id} onChange={(e) => setGen({ ...gen, client_id: e.target.value })} /></label>
          </div>
          <button className="btn btn-primary" disabled={!gen.host} onClick={() => run(() => api.generateServer(gen), setGenerated)}>Generate</button>
          {generated && (
            <>
              <h3>Merged server.json {generated.basedOnDevice ? '(based on the file on your device)' : '(new file)'}</h3>
              <pre className="json">{JSON.stringify(generated.merged, null, 2)}</pre>
              <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(JSON.stringify(generated.merged, null, 2)).then(() => toast('Copied', 'ok'))}>Copy</button>
              <pre className="instructions">{generated.instructions}</pre>
            </>
          )}
        </Card>
      </div>

      <Card title="Browse a Home Assembly 64 server">
        <form className="type-form" onSubmit={(e) => { e.preventDefault(); run(() => api.homeSearch(server, query), setResults) }}>
          <input value={server} onChange={(e) => setServer(e.target.value)} aria-label="Server URL" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Title…" aria-label="Search" />
          <button className="btn btn-primary" disabled={!query}>Search</button>
        </form>
        {results && (results.length ? (
          <ul className="file-list">
            {results.map((r: any) => (
              <li key={`${r.id}-${r.category}`}>
                <span className="tag">{r.type ?? r.category}</span>
                <strong>{r.name}</strong> <span className="muted">{[r.group, r.year].filter(Boolean).join(' · ')}</span>
                <button className="btn btn-sm" onClick={() => run(() => api.homeEntries(server, String(r.id), String(r.category)),
                  (list) => setEntries((m) => ({ ...m, [`${r.id}`]: list })))}>Files</button>
                {entries[`${r.id}`]?.map((f: any) => (
                  <div key={f.id} className="indent">
                    <span className="mono small">{f.path}</span>
                    <button className="btn btn-sm" onClick={() => run(() => api.homeImport({
                      server, entry_id: String(r.id), category: String(r.category), content_id: String(f.id), filename: f.path.split('/').pop(),
                    }), (res) => toast(`Added ${res.path}`, 'ok'))}>Add to library</button>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        ) : <Empty>No results.</Empty>)}
      </Card>
    </div>
  )
}
