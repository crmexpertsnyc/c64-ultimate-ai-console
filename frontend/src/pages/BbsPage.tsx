import { Fragment, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Empty, Modal, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'
import { bbsApi, connectionText } from '../services/bbsApi'
import type { Bbs, BbsHardware, BbsList, Compat } from '../services/bbsApi'
import './bbs.css'

/**
 * 📟 BBS — public telnet bulletin boards: a searchable directory, ⭐ favorites and 📝 notes, and a browser terminal.
 * Admins (this computer, or signed in with the console password) approve boards before they can be opened.
 */

const COMPAT_LABEL: Record<Compat, string> = { confirmed: 'confirmed', unverified: 'listed, not verified', unknown: 'unknown' }

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text)
  // plain-http pages have no clipboard API: fall back to a hidden textarea
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'; ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    if (ok) resolve(); else reject(new Error('copy failed'))
  })
}

const ago = (iso: string | null) => {
  if (!iso) return null
  const d = (Date.now() - new Date(iso).getTime()) / 86400000
  if (d < 1) return 'today'
  if (d < 2) return 'yesterday'
  if (d < 45) return `${Math.floor(d)} days ago`
  return new Date(iso).toLocaleDateString()
}

function CompatBadge({ kind, value }: { kind: 'PETSCII' | 'ANSI'; value: Compat }) {
  return (
    <span className={`bbs-compat is-${value}`} title={`${kind}: ${COMPAT_LABEL[value]}`}>
      {kind}{value === 'confirmed' ? ' ✓' : value === 'unverified' ? ' ?' : ' –'}
    </span>
  )
}

function StatusLine({ b }: { b: Bbs }) {
  if (!b.lastCheckAt) return <span className="muted small">Not checked yet</span>
  const ok = b.status === 'reachable'
  return (
    <span className="muted small" title="A connection check, not proof the board works">
      <span className={`bbs-dot ${ok ? 'ok' : 'bad'}`} /> {ok ? 'Answered' : 'No answer'} {ago(b.lastCheckAt)}
      {!ok && b.lastOkAt ? ` · last answered ${ago(b.lastOkAt)}` : ''}
    </span>
  )
}

function BoardCard({ b, onOpen, onFav, onConnect }: { b: Bbs; onOpen: () => void; onFav: () => void; onConnect: () => void }) {
  return (
    <li className={`bbs-card ${b.approved ? '' : 'is-pending'}`}>
      <div className="bbs-card-top">
        <button className="bbs-name link" onClick={onOpen}>{b.name}</button>
        <button className={`bbs-star ${b.favorite ? 'on' : ''}`} onClick={onFav} aria-label={b.favorite ? 'Remove from favorites' : 'Add to favorites'}
          title={b.favorite ? 'Favorite' : 'Add to favorites'}>{b.favorite ? '★' : '☆'}</button>
      </div>
      {b.description && <p className="bbs-desc small">{b.description}</p>}
      <div className="bbs-meta small">
        <code>{b.host}:{b.port}</code>
        {b.location && <span>📍 {b.location}</span>}
      </div>
      <div className="bbs-badges">
        <CompatBadge kind="PETSCII" value={b.petscii} />
        <CompatBadge kind="ANSI" value={b.ansi} />
        {!b.approved && <span className="bbs-compat is-pending">awaiting approval</span>}
      </div>
      <StatusLine b={b} />
      <div className="bbs-actions">
        <button className="btn btn-primary btn-sm" onClick={onConnect} disabled={!b.approved}
          title={b.approved ? 'Open it in the browser terminal' : 'An admin has to approve this board first'}>▶ Connect in browser</button>
        <button className="btn btn-sm" onClick={onOpen}>Details</button>
        {b.website && <a className="btn btn-ghost btn-sm" href={b.website} target="_blank" rel="noopener noreferrer nofollow">Website ↗</a>}
      </div>
    </li>
  )
}

function Details({ b, admin, onClose, onChanged }: { b: Bbs; admin: boolean; onClose: () => void; onChanged: (b: Partial<Bbs> & { id: number }) => void }) {
  const toast = useToast()
  const navigate = useNavigate()
  const [notes, setNotes] = useState(b.notes)
  const [busy, setBusy] = useState('')
  useEffect(() => setNotes(b.notes), [b.id, b.notes])

  const run = async (what: string, fn: () => Promise<Partial<Bbs> | void>, ok?: string) => {
    setBusy(what)
    try {
      const res = await fn()
      if (res) onChanged({ id: b.id, ...res })
      if (ok) toast(ok, 'ok')
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy('') }
  }
  const copy = () => copyText(connectionText(b)).then(() => toast('Connection details copied', 'ok'), () => toast("Couldn't copy — select the text instead", 'error'))

  return (
    <Modal open title={b.name} onClose={onClose} footer={
      <>
        <button className="btn" onClick={copy}>📋 Copy connection details</button>
        <button className="btn btn-primary" disabled={!b.approved} onClick={() => navigate(`/bbs/${b.id}/terminal`)}>▶ Connect in browser</button>
      </>
    }>
      <div className="bbs-detail">
        {b.description && <p>{b.description}</p>}
        <dl className="bbs-dl">
          <dt>Address</dt><dd><code>{b.host}</code> port <code>{b.port}</code> ({b.protocol === 'raw' ? 'raw TCP' : 'telnet'})</dd>
          {b.location && <><dt>Location</dt><dd>{b.location}</dd></>}
          {b.software && <><dt>Software</dt><dd>{b.software}</dd></>}
          {b.website && <><dt>Website</dt><dd><a href={b.website} target="_blank" rel="noopener noreferrer nofollow">{b.website} ↗</a></dd></>}
          <dt>Terminal</dt><dd>
            PETSCII: <b>{COMPAT_LABEL[b.petscii]}</b> · ANSI: <b>{COMPAT_LABEL[b.ansi]}</b>
            {b.compatNote && <div className="muted small">{b.compatNote}</div>}
          </dd>
          <dt>Reachability</dt><dd><StatusLine b={b} />
            {admin && b.lastError && <div className="muted small">Last error: {b.lastError}</div>}</dd>
          <dt>Listed by</dt><dd>
            {b.sources.length === 0 && <span className="muted">—</span>}
            {b.sources.map((s) => (
              <div key={s.source}>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.label} ↗</a> : s.label}
                <span className="muted small"> · seen {s.seenAt}{s.listingUpdated ? ` · list of ${s.listingUpdated}` : ''}</span></div>
            ))}
            {!b.listed && <div className="muted small">No longer in its source list — it may have closed.</div>}
          </dd>
        </dl>

        <h3>How to connect</h3>
        <ul className="small">
          <li><b>In this browser:</b> press ▶ Connect. Use <b>PETSCII</b> mode for Commodore boards, <b>ANSI</b> for PC-style boards.</li>
          <li><b>From a computer:</b> any telnet client, e.g. <code>telnet {b.host} {b.port}</code> (SyncTERM is a good one).</li>
          <li><b>From your real C64:</b> in CCGMS or StrikeTerm, type <code>ATDT {b.host}:{b.port}</code> — see the <i>On your C64</i> tab.</li>
        </ul>
        <p className="muted small">🔓 Telnet isn't encrypted: don't reuse a password you use anywhere else. The console doesn't save BBS passwords.</p>

        <div className="bbs-mine">
          <button className={`btn btn-sm ${b.favorite ? 'btn-primary' : ''}`} disabled={busy === 'fav'}
            onClick={() => run('fav', () => bbsApi.setMine(b.id, { favorite: !b.favorite }))}>{b.favorite ? '★ Favorite' : '☆ Add to favorites'}</button>
          <label className="bbs-notes">📝 Your notes <span className="muted small">(only on this console — no passwords, please)</span>
            <textarea rows={3} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Handle, favorite doors, best time to call…" />
          </label>
          <button className="btn btn-sm" disabled={busy === 'notes' || notes === b.notes}
            onClick={() => run('notes', () => bbsApi.setMine(b.id, { notes }), 'Notes saved')}>Save notes</button>
        </div>

        {admin && (
          <div className="bbs-admin-box">
            <h3>🛡 Admin</h3>
            <div className="bbs-row">
              <span>Review: <b>{b.review}</b></span>
              {b.review !== 'approved' && <button className="btn btn-sm btn-primary" disabled={!!busy} onClick={() => run('rv', () => bbsApi.review(b.id, 'approve'), 'Approved')}>Approve</button>}
              {b.review !== 'rejected' && <button className="btn btn-sm" disabled={!!busy} onClick={() => run('rv', () => bbsApi.review(b.id, 'reject'), 'Rejected')}>Reject</button>}
              <button className="btn btn-sm" disabled={!!busy} onClick={() => run('chk', () => bbsApi.check(b.id))}>{busy === 'chk' ? <Spinner /> : '🔌'} Check now</button>
            </div>
            <div className="bbs-row">
              <label>Protocol <select value={b.protocol} onChange={(e) => run('ed', () => bbsApi.edit(b.id, { protocol: e.target.value as 'telnet' | 'raw' }))}>
                <option value="telnet">telnet</option><option value="raw">raw TCP</option></select></label>
              <label>PETSCII <select value={b.petscii} onChange={(e) => run('ed', () => bbsApi.edit(b.id, { petscii: e.target.value as Compat }))}>
                <option value="confirmed">confirmed (I tested it)</option><option value="unverified">unverified</option><option value="unknown">unknown</option></select></label>
              <label>ANSI <select value={b.ansi} onChange={(e) => run('ed', () => bbsApi.edit(b.id, { ansi: e.target.value as Compat }))}>
                <option value="confirmed">confirmed (I tested it)</option><option value="unverified">unverified</option><option value="unknown">unknown</option></select></label>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

function Intro() {
  return (
    <details className="card bbs-intro">
      <summary><b>What's a BBS?</b> <span className="muted small">— a 60-second explanation</span></summary>
      <p>A <b>bulletin board system</b> is a computer you call up to read and post messages, chat, play turn-based "door" games and
        swap files. In the 1980s you dialed one over the phone with a modem — the C64 was one of the most popular machines to do it with.</p>
      <p>Hundreds of boards still run today, reachable over the internet with <b>telnet</b>. Commodore boards draw their screens in
        <b> PETSCII</b> (the C64's own colourful character set); PC-style boards use <b>ANSI</b>. Most ask you to create a free account the
        first time — pick a handle (nickname) and a password you don't use anywhere else.</p>
    </details>
  )
}

function HardwareTab() {
  const [hw, setHw] = useState<BbsHardware | null>(null)
  useEffect(() => { bbsApi.hardware().then(setHw).catch(() => setHw({ dialOnC64: false, modem: null, error: "couldn't read" })) }, [])
  const m = hw?.modem ?? {}
  return (
    <div className="bbs-hw">
      <Card title="☎ Call a BBS from your real C64">
        <p>The C64 Ultimate has a built-in modem emulation: your C64 terminal program "dials" a BBS by its internet address. Nothing
          here changes your C64 Ultimate's settings — set them yourself in its menu (F2 → Modem Settings) if needed.</p>
        <h3>Your C64 Ultimate's modem settings <span className="muted small">(read only)</span></h3>
        {!hw ? <Spinner /> : hw.error ? <p className="muted small">{hw.error}</p> : (
          <dl className="bbs-dl small">{Object.entries(m).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
        )}
        <h3>Steps</h3>
        <ol className="small">
          <li>In the Ultimate menu (<b>F2</b>), <b>Modem Settings</b>: Modem Interface <b>ACIA / SwiftLink</b>, ACIA address <b>DE00</b>,
            NMI. (Yours is shown above.)</li>
          <li>Load a terminal program that supports SwiftLink, e.g. <b>CCGMS 2021</b> or <b>StrikeTerm 2014</b>, and pick
            <b> SwiftLink / DE00</b> as the modem with a speed of 2400–9600 baud.</li>
          <li>In the terminal, type <code>AT</code> and RETURN — the modem should answer <code>OK</code>.</li>
          <li>Dial a board: <code>ATDT host:port</code>, e.g. the address shown on a board's card. Use <b>Copy connection details</b>.</li>
          <li>To hang up: <code>+++</code>, wait, then <code>ATH</code>.</li>
        </ol>
      </Card>
      <Card title="✅ Test checklist (for 'Dial on my C64')">
        <p className="small">"Dial on my C64" (the console typing the dial command for you) stays <b>off</b> until all four are confirmed on real
          hardware: {hw?.dialOnC64 ? <b>it's switched on (BBS_DIAL_ON_C64).</b> : <span className="muted">currently off.</span>}</p>
        <ol className="small">
          <li>The terminal program launches from your library on the real C64.</li>
          <li>The terminal program finds the modem (AT → OK) with the Ultimate's ACIA / SwiftLink settings.</li>
          <li>Text typed from the app (Controller → keyboard) arrives in the terminal reliably, including : and digits.</li>
          <li>ATDT to an approved board connects and shows its login screen.</li>
        </ol>
      </Card>
    </div>
  )
}

function ManageTab({ data, reload }: { data: BbsList; reload: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const [form, setForm] = useState({ name: '', host: '', port: '23', protocol: 'telnet', source_url: '', website: '' })
  const refresh = async () => {
    setBusy('refresh')
    try {
      const r = await bbsApi.refresh()
      toast(r.errors?.length ? `Couldn't reach ${r.errors.join(', ')}` : `${r.added} new, ${r.updated} updated`, r.errors?.length ? 'error' : 'ok')
      reload()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy('') }
  }
  const toggle = async (setting: string, on: boolean) => {
    try { await api.saveSettings({ [setting]: on } as never); toast('Saved', 'ok'); reload() } catch (e) { toast(errorMessage(e), 'error') }
  }
  const add = async () => {
    setBusy('add')
    try {
      await bbsApi.add({ ...form, port: Number(form.port) || 23, protocol: form.protocol as 'telnet' | 'raw' })
      toast('Added — approve it below to enable connections', 'ok')
      setForm({ name: '', host: '', port: '23', protocol: 'telnet', source_url: '', website: '' })
      reload()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy('') }
  }
  return (
    <div className="bbs-manage">
      <Card title="🔄 Directory sources" actions={<button className="btn btn-primary btn-sm" onClick={refresh} disabled={!!busy}>{busy === 'refresh' ? <Spinner /> : '🔄'} Refresh now</button>}>
        <p className="muted small">Refreshed monthly by itself (Settings → Sources & updates). {data.lastImport ? `Last refresh ${ago(data.lastImport.at)}.` : 'Never refreshed yet.'}</p>
        <ul className="bbs-sources">
          {data.sources.map((s) => {
            const res = data.lastImport?.results?.[s.name]
            const setting = s.name === 'syncterm' ? 'BBS_SOURCE_SYNCTERM' : 'BBS_SOURCE_TBG'
            return (
              <li key={s.name}>
                <label className="bbs-toggle"><input type="checkbox" checked={s.enabled} onChange={(e) => toggle(setting, e.target.checked)} />
                  <b>{s.label}</b></label> <a className="small" href={s.url} target="_blank" rel="noopener noreferrer">{s.url} ↗</a>
                <div className="muted small">{s.terms}</div>
                {res && <div className="small">{res.error ? `⚠ ${res.error}` : `${res.records} boards listed · ${res.added} new`}</div>}
              </li>
            )
          })}
        </ul>
      </Card>
      <Card title="➕ Add a board by hand">
        <div className="bbs-form">
          <label>Name<input value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Host<input value={form.host} maxLength={255} placeholder="bbs.example.com" onChange={(e) => setForm({ ...form, host: e.target.value })} /></label>
          <label>Port<input value={form.port} inputMode="numeric" onChange={(e) => setForm({ ...form, port: e.target.value })} /></label>
          <label>Protocol<select value={form.protocol} onChange={(e) => setForm({ ...form, protocol: e.target.value })}><option value="telnet">telnet</option><option value="raw">raw TCP</option></select></label>
          <label className="wide">Where you found it (link)<input value={form.source_url} maxLength={400} placeholder="https://…" onChange={(e) => setForm({ ...form, source_url: e.target.value })} /></label>
        </div>
        <button className="btn btn-primary btn-sm" onClick={add} disabled={!!busy || !form.name || !form.host}>Add (as pending)</button>
      </Card>
    </div>
  )
}

export function BbsPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'directory'
  const [q, setQ] = useState('')
  const [terminal, setTerminal] = useState<'' | 'petscii' | 'ansi'>('')
  const [favorites, setFavorites] = useState(false)
  const [reachable, setReachable] = useState(false)
  const [review, setReview] = useState('')
  const [data, setData] = useState<BbsList | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  const load = useCallback(() => {
    bbsApi.list({ q: q || undefined, terminal: terminal || undefined, favorites, reachable, review: review || undefined })
      .then(setData).catch((e) => toast(errorMessage(e), 'error'))
  }, [q, terminal, favorites, reachable, review, toast])
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t) }, [load, q])

  const patch = (p: Partial<Bbs> & { id: number }) =>
    setData((d) => d && { ...d, boards: d.boards.map((b) => (b.id === p.id ? { ...b, ...p } : b)) })
  const fav = (b: Bbs) => bbsApi.setMine(b.id, { favorite: !b.favorite }).then((r) => patch({ id: b.id, ...r })).catch((e) => toast(errorMessage(e), 'error'))
  const current = data?.boards.find((b) => b.id === open) ?? null
  const setTab = (t: string) => setParams(t === 'directory' ? {} : { tab: t }, { replace: true })

  return (
    <div className="page bbs-page">
      <header className="page-head">
        <h1>📟 BBS</h1>
        <div className="chips bbs-tabs">
          <button className={`chip ${tab === 'directory' ? 'on' : ''}`} onClick={() => setTab('directory')}>Directory</button>
          <button className={`chip ${tab === 'c64' ? 'on' : ''}`} onClick={() => setTab('c64')}>☎ On your C64</button>
          {data?.admin && <button className={`chip ${tab === 'manage' ? 'on' : ''}`} onClick={() => setTab('manage')}>
            🛡 Manage{data.counts.pending ? ` (${data.counts.pending} pending)` : ''}</button>}
        </div>
      </header>

      {tab === 'c64' && <HardwareTab />}
      {tab === 'manage' && data?.admin && <ManageTab data={data} reload={load} />}
      {tab === 'directory' && (
        <>
          <Intro />
          <Card className="bbs-filters">
            <input type="search" placeholder="Search name, description or location…" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} aria-label="Search boards" />
            <div className="chips">
              <button className={`chip ${terminal === '' ? 'on' : ''}`} onClick={() => setTerminal('')}>All</button>
              <button className={`chip ${terminal === 'petscii' ? 'on' : ''}`} onClick={() => setTerminal('petscii')} title="Confirmed or listed as PETSCII">🟦 PETSCII</button>
              <button className={`chip ${terminal === 'ansi' ? 'on' : ''}`} onClick={() => setTerminal('ansi')} title="Confirmed or listed as ANSI/ASCII">⬛ ANSI / ASCII</button>
              <button className={`chip ${favorites ? 'on' : ''}`} onClick={() => setFavorites(!favorites)}>★ Favorites</button>
              <button className={`chip ${reachable ? 'on' : ''}`} onClick={() => setReachable(!reachable)} title="Answered a connection check in the last 30 days">🔌 Recently reachable</button>
              {data?.admin && (
                <select value={review} onChange={(e) => setReview(e.target.value)} aria-label="Approval filter">
                  <option value="">Approved + pending</option><option value="pending">Pending only</option>
                  <option value="approved">Approved only</option><option value="rejected">Rejected</option>
                </select>
              )}
            </div>
            <p className="muted small bbs-legend">PETSCII / ANSI: <b>✓</b> confirmed by a test · <b>?</b> listed by its source, not verified · <b>–</b> unknown.
              "Answered" means the board's address accepted a connection — not that the board is fully working.</p>
          </Card>

          {!data ? <Spinner /> : data.setupPending ? (
            <Empty>
              <b>Directory setup pending.</b> No board list has been imported yet{data.lastImport ? ' — the last attempt couldn\'t reach its source' : ''}.
              {data.admin ? <> Open <button className="link" onClick={() => setTab('manage')}>🛡 Manage</button> and press Refresh now.</> : ' An admin needs to refresh it on the console.'}
            </Empty>
          ) : data.boards.length === 0 ? (
            <Empty>{data.counts.approved === 0 && !data.admin
              ? 'No boards have been approved for browser connections yet — an admin approves them on the console (this computer, or signed in with the console password).'
              : 'No boards match.'}</Empty>
          ) : (
            <ul className="bbs-grid">
              {data.boards.map((b) => (
                <BoardCard key={b.id} b={b} onOpen={() => setOpen(b.id)} onFav={() => fav(b)} onConnect={() => navigate(`/bbs/${b.id}/terminal`)} />
              ))}
            </ul>
          )}
          <p className="muted small">Board lists from <a href="https://syncterm.bbsdev.net/" target="_blank" rel="noopener noreferrer">SyncTERM's directory ↗</a>
            {data?.sources.find((s) => s.name === 'tbg' && s.enabled) && <> and the <a href="https://www.telnetbbsguide.com" target="_blank" rel="noopener noreferrer">Telnet BBS Guide ↗</a></>}.
            {' '}Each board links back to where it was listed. <Link to="/bbs?tab=c64">Calling from a real C64?</Link></p>
        </>
      )}
      {current && <Details b={current} admin={!!data?.admin} onClose={() => setOpen(null)} onChanged={(p) => { patch(p); if ('review' in p) load() }} />}
    </div>
  )
}
