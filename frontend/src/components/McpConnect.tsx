import { useEffect, useMemo, useState } from 'react'
import { api, errorMessage } from '../services/api'
import { Card } from './common'
import { useToast } from './Toasts'

interface McpInfo {
  enabled: boolean
  localUrl: string
  lanUrls: string[]
  tokenSet: boolean
  allowedHosts: string
  stdio: { python: string; script: string; apiBase: string }
  tools: string[]
}

type Client = 'claude-code' | 'claude-desktop' | 'desktop-remote' | 'vscode' | 'cursor' | 'inspector'

const CLIENTS: { id: Client; label: string }[] = [
  { id: 'claude-code', label: 'Claude Code' },
  { id: 'claude-desktop', label: 'Claude Desktop' },
  { id: 'desktop-remote', label: 'Claude Desktop (other PC)' },
  { id: 'vscode', label: 'VS Code' },
  { id: 'cursor', label: 'Cursor' },
  { id: 'inspector', label: 'MCP Inspector' },
]

function newToken(): string {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  return 'c64_' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** Settings card: status of the built-in MCP endpoint and copy-ready setup for common clients. */
export function McpConnect() {
  const toast = useToast()
  const [info, setInfo] = useState<McpInfo | null>(null)
  const [client, setClient] = useState<Client>('claude-code')
  const [lan, setLan] = useState(false)
  const [freshToken, setFreshToken] = useState<string | null>(null)
  const [hosts, setHosts] = useState('')

  const load = () => api.mcpInfo().then((i) => { setInfo(i); setHosts(i.allowedHosts) }).catch(() => {})
  useEffect(() => { load() }, [])
  // Deep link /settings#mcp: this card loads after the page, so scroll once it is rendered.
  useEffect(() => {
    if (info && window.location.hash === '#mcp') {
      document.getElementById('mcp')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [info])

  const url = lan ? (info?.lanUrls[0] ?? info?.localUrl ?? '') : (info?.localUrl ?? '')
  const token = freshToken ?? (info?.tokenSet ? '<your MCP token>' : null)

  const snippet = useMemo(() => {
    if (!info) return ''
    const bearer = token ? `Bearer ${token}` : null
    switch (client) {
      case 'claude-code':
        return `claude mcp add --transport http --scope user c64 ${url}` + (bearer ? ` --header "Authorization: ${bearer}"` : '')
          + `\n\n# check it:\nclaude mcp list`
      case 'claude-desktop':
        return JSON.stringify({ mcpServers: { c64: {
          command: info.stdio.python, args: [info.stdio.script], env: { MCP_API_BASE: info.stdio.apiBase },
        } } }, null, 2)
      case 'desktop-remote':
        return JSON.stringify({ mcpServers: { c64: {
          command: 'npx',
          args: ['-y', 'mcp-remote', url, '--allow-http', ...(bearer ? ['--header', 'Authorization:${AUTH_HEADER}'] : [])],
          ...(bearer ? { env: { AUTH_HEADER: bearer } } : {}),
        } } }, null, 2)
      case 'vscode':
        return JSON.stringify({ servers: { c64: { type: 'http', url, ...(bearer ? { headers: { Authorization: bearer } } : {}) } } }, null, 2)
      case 'cursor':
        return JSON.stringify({ mcpServers: { c64: { url, ...(bearer ? { headers: { Authorization: bearer } } : {}) } } }, null, 2)
      case 'inspector':
        return `npx @modelcontextprotocol/inspector\n\n# then choose "Streamable HTTP", URL ${url}` + (bearer ? `\n# and add the header  Authorization: ${bearer}` : '')
    }
  }, [client, info, url, token])

  const where: Record<Client, string> = {
    'claude-code': 'Run in a terminal. --scope user makes it available in every project.',
    'claude-desktop': 'Paste into claude_desktop_config.json (Windows: %APPDATA%\\Claude\\ · macOS: ~/Library/Application Support/Claude/), then restart Claude Desktop. Works on this PC.',
    'desktop-remote': 'For Claude Desktop on another computer (needs Node.js). Set an MCP token first — other machines are refused without one.',
    vscode: 'Save as .vscode/mcp.json in a workspace (or add to your user MCP settings).',
    cursor: 'Save as ~/.cursor/mcp.json (all projects) or .cursor/mcp.json (one project).',
    inspector: 'Official test tool: lists tools and lets you call them by hand.',
  }

  const saveToken = async (value: string | null) => {
    try {
      await api.saveSettings(value ? { MCP_TOKEN: value } : { CLEAR_MCP_TOKEN: true })
      setFreshToken(value)
      await load()
      toast(value ? 'Token saved — copy it now, it will not be shown again' : 'Token removed', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const saveHosts = async () => {
    try {
      await api.saveSettings({ MCP_ALLOWED_HOSTS: hosts })
      await load()
      toast('Allowed hosts saved', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  if (!info) return null
  return (
    <Card title="Connect AI agents (MCP)">
      <div id="mcp" />
      {!info.enabled ? (
        <p className="muted">The MCP endpoint is off (set <code>MCP_ENABLED=true</code> and install the <code>mcp</code> package).</p>
      ) : (
        <>
          <p className="muted small">
            AI assistants (Claude, VS Code, Cursor…) can use {info.tools.length} safe tools: play games, search the catalog,
            see the screen, swap disks, record, and more. Memory access, settings, firmware and power-off are never exposed.
          </p>
          <dl className="kv">
            <dt>This PC</dt><dd className="mono">{info.localUrl}</dd>
            <dt>Other devices</dt><dd className="mono">{info.lanUrls.join('  ·  ')}</dd>
            <dt>Security</dt>
            <dd>{info.tokenSet ? '🔒 Token required' : '🏠 This PC only (no token)'}{info.allowedHosts ? ` · also allowed: ${info.allowedHosts}` : ''}</dd>
          </dl>

          <div className="row-actions">
            <button className="btn" onClick={() => saveToken(newToken())}>{info.tokenSet ? '↻ New token' : '🔒 Create token'}</button>
            {info.tokenSet && <button className="btn btn-ghost" onClick={() => saveToken(null)}>Remove token</button>}
            <label className="field mcp-hosts"><span>Allowed hosts without token</span>
              <input value={hosts} onChange={(e) => setHosts(e.target.value)} placeholder="e.g. spark-2d40" /></label>
            <button className="btn btn-ghost btn-sm" disabled={hosts === info.allowedHosts} onClick={saveHosts}>Save</button>
          </div>
          {freshToken && (
            <div className="banner banner-warn small">
              New token: <code className="mono">{freshToken}</code>{' '}
              <button className="link" onClick={() => navigator.clipboard.writeText(freshToken).then(() => toast('Token copied', 'ok'))}>Copy</button>
              {' '}— it is included in the snippets below until you leave this page.
            </div>
          )}

          <div className="seg mcp-tabs" role="tablist">
            {CLIENTS.map((c) => (
              <button key={c.id} role="tab" aria-selected={client === c.id} className={`seg-btn ${client === c.id ? 'on' : ''}`}
                onClick={() => setClient(c.id)}>{c.label}</button>
            ))}
          </div>
          {client !== 'claude-desktop' && (
            <label className="toggle"><input type="checkbox" checked={lan} onChange={(e) => setLan(e.target.checked)} /> Connecting from another device</label>
          )}
          <p className="muted small">{where[client]}</p>
          <pre className="json mcp-snippet">{snippet}</pre>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={() => navigator.clipboard.writeText(snippet).then(() => toast('Copied', 'ok'))}>Copy</button>
            <a className="btn btn-ghost" href="https://github.com/modelcontextprotocol" target="_blank" rel="noreferrer">About MCP</a>
          </div>
          <details className="small">
            <summary>Available tools ({info.tools.length})</summary>
            <p className="mono">{info.tools.join(', ')}</p>
          </details>
        </>
      )}
    </Card>
  )
}
