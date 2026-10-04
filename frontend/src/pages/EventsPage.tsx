import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { errorMessage } from '../services/api'
import { eventsApi } from '../services/eventsApi'
import type { EventInput, EventList, RetroEvent } from '../services/eventsApi'
import './events.css'

const TYPES: { id: string; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'party', label: '🎉 Parties' },
  { id: 'meeting', label: '🤝 Meetings' },
  { id: 'compo', label: '🏆 Compos' },
  { id: 'expo', label: '🕹 Retro gaming expos' },
  { id: 'festival', label: '🖥 Vintage computer festivals' },
  { id: 'conference', label: '🎤 Conferences' },
  { id: 'convention', label: '🎪 Conventions' },
  { id: 'pinball', label: '🎰 Arcade & pinball' },
  { id: 'fair', label: '🏛 Fairs & swap meets' },
]
const SOURCE_HINT: Record<string, string> = {
  csdb: 'From the CSDb (C64 Scene Database) events calendar',
  web: 'Found by 🤖 web research — check the website before you travel',
  user: 'You added this one',
}

const WHEN: { id: string; label: string; days?: number }[] = [
  { id: '', label: 'Any time' }, { id: 'month', label: 'This month' }, { id: '30', label: 'Next 30 days', days: 30 },
  { id: '90', label: 'Next 3 months', days: 90 }, { id: '180', label: 'Next 6 months', days: 180 },
  { id: '365', label: 'Next 12 months', days: 365 }, { id: 'custom', label: 'Custom dates…' },
]
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
function dateWindow(id: string, from: string, to: string): { from?: string; to?: string } {
  const today = new Date()
  if (id === 'month') return { from: isoDay(today), to: isoDay(new Date(today.getFullYear(), today.getMonth() + 1, 0)) }
  const w = WHEN.find((x) => x.id === id)
  if (w?.days) return { from: isoDay(today), to: isoDay(new Date(today.getTime() + w.days * 86400000)) }
  if (id === 'custom') return { from: from || undefined, to: to || undefined }
  return {}
}

const parseDay = (iso: string) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d) }
const monthKey = (iso: string) => iso.slice(0, 7)
const monthLabel = (key: string) => parseDay(`${key}-01`).toLocaleDateString([], { month: 'long', year: 'numeric' })
const mon = (d: Date) => d.toLocaleDateString([], { month: 'short' })

/** "2–4 Oct", "30 Apr – 2 May", "28 Nov" */
function dateRange(e: RetroEvent): string {
  const s = parseDay(e.start)
  const t = parseDay(e.end)
  if (e.start === e.end) return `${s.getDate()} ${mon(s)}`
  if (s.getFullYear() !== t.getFullYear()) return `${s.getDate()} ${mon(s)} ${s.getFullYear()} – ${t.getDate()} ${mon(t)} ${t.getFullYear()}`
  if (s.getMonth() !== t.getMonth()) return `${s.getDate()} ${mon(s)} – ${t.getDate()} ${mon(t)}`
  return `${s.getDate()}–${t.getDate()} ${mon(s)}`
}

function daysUntil(iso: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((parseDay(iso).getTime() - today.getTime()) / 86400000)
}

function when(e: RetroEvent): string {
  if (e.ongoing) return e.live ? 'happening now' : 'on now'
  const n = daysUntil(e.start)
  if (n < 0) return 'over'
  if (n === 0) return 'today'
  if (n === 1) return 'tomorrow'
  if (n < 14) return `in ${n} days`
  if (n < 60) return `in ${Math.round(n / 7)} weeks`
  return ''
}

const EMPTY_FORM: EventInput = { name: '', start: '', end: '', city: '', country: '', url: '', type: '', notes: '' }

/** 📅 Retro events worldwide: demoparties (CSDb), fairs, expos and meetups (🤖 web research) and your own. */
export function EventsPage() {
  const toast = useToast()
  const { news } = useLive()
  const [data, setData] = useState<EventList | null>(null)
  const [type, setType] = useState('')
  const [country, setCountry] = useState('')
  const [region, setRegion] = useState('')
  const [scope, setScope] = useState<'' | 'commodore' | 'retro'>('')
  const [whenId, setWhenId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [homeFirst, setHomeFirst] = useState(() => { try { return localStorage.getItem('c64.eventsHomeFirst') !== '0' } catch { return true } })
  const [q, setQ] = useState('')
  const [past, setPast] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const [busy, setBusy] = useState<'refresh' | 'research' | 'save' | number | null>(null)
  const [form, setForm] = useState<(EventInput & { id?: number }) | null>(null)

  const load = useCallback(() => {
    eventsApi.list({ type: type || undefined, country: country || undefined, q: q.trim() || undefined,
      include_past: past, include_hidden: showHidden, region: region || undefined, scope: scope || undefined,
      home_first: homeFirst, ...dateWindow(whenId, from, to) })
      .then(setData).catch((e) => toast(errorMessage(e), 'error'))
  }, [type, country, q, past, showHidden, region, scope, homeFirst, whenId, from, to, toast])
  useEffect(() => { try { localStorage.setItem('c64.eventsHomeFirst', homeFirst ? '1' : '0') } catch { /* private mode */ } }, [homeFirst])
  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t) }, [load, q, news?.at])

  const months = useMemo(() => {
    const groups: { key: string; items: RetroEvent[] }[] = []
    for (const e of (data?.items ?? []).filter((x) => !(homeFirst && x.home && !country))) {
      const key = monthKey(e.ongoing && !past ? new Date().toISOString().slice(0, 10) : e.start)
      const g = groups[groups.length - 1]
      if (g && g.key === key) g.items.push(e)
      else groups.push({ key, items: [e] })
    }
    return groups
  }, [data, past, homeFirst, country])
  const homeEvents = homeFirst && !country ? (data?.items ?? []).filter((e) => e.home) : []

  const refresh = async () => {
    setBusy('refresh')
    try {
      const r = await eventsApi.refresh()
      if (Object.keys(r.errors).length) toast(`Couldn't reach ${Object.keys(r.errors).join(', ')} — try again later`, 'error')
      else toast(r.added ? `📅 ${r.added} new event${r.added === 1 ? '' : 's'} from CSDb` : 'Up to date — no new CSDb events', 'ok')
      load()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }
  const research = async () => {
    setBusy('research')
    try {
      const r = await eventsApi.research()
      toast(r.added ? `🤖 Found ${r.added} new event${r.added === 1 ? '' : 's'}` : '🤖 Nothing new found this time', 'ok')
      load()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }
  const copySubscribe = async () => {
    const url = eventsApi.calendarUrl()
    try {
      await navigator.clipboard.writeText(url)
      toast('📋 Calendar link copied — add it as a subscription in your calendar app', 'ok')
    } catch {
      window.prompt('Copy this calendar link:', url)
    }
  }
  const hide = async (e: RetroEvent, hidden: boolean) => {
    setBusy(e.id)
    try { await eventsApi.edit(e.id, { hidden }); toast(hidden ? `Hidden: ${e.name}` : `Showing ${e.name} again`, 'ok'); load() }
    catch (err) { toast(errorMessage(err), 'error') } finally { setBusy(null) }
  }
  const remove = async (e: RetroEvent) => {
    if (!window.confirm(`Delete "${e.name}"?`)) return
    setBusy(e.id)
    try { await eventsApi.remove(e.id); load() } catch (err) { toast(errorMessage(err), 'error') } finally { setBusy(null) }
  }
  const edit = (e: RetroEvent) => setForm({ id: e.id, name: e.name, start: e.start, end: e.end, city: e.city ?? '', country: e.country ?? '',
    url: e.url ?? '', type: e.type ?? '', notes: e.notes ?? '' })
  const save = async (ev: FormEvent) => {
    ev.preventDefault()
    if (!form) return
    const body: EventInput = {
      name: form.name.trim(), start: form.start, end: form.end || null, city: form.city?.trim() || null,
      country: form.country?.trim() || null, url: form.url?.trim() || null, type: form.type?.trim() || null, notes: form.notes?.trim() || null,
    }
    setBusy('save')
    try {
      if (form.id) await eventsApi.edit(form.id, body)
      else await eventsApi.add(body)
      toast(form.id ? 'Event saved' : `📅 Added ${body.name}`, 'ok')
      setForm(null)
      load()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }

  const st = data?.state
  const set = (k: keyof EventInput) => (e: { target: { value: string } }) => setForm((f) => f && { ...f, [k]: e.target.value })
  return (
    <div className="page events-page">
      <header className="page-head">
        <div>
          <h1>📅 Retro events</h1>
          <p className="muted">Retro gaming expos, vintage computer festivals, conferences, demoparties and meetups — {data?.homeCountry ?? 'your country'} first, then the world.
            {' '}<Link to="/settings#updates">🔄 Updated automatically</Link>.
            {st?.csdbAt ? ` CSDb checked ${new Date(st.csdbAt).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}.` : ''}</p>
        </div>
        <div className="row-actions">
          <button className="btn btn-ghost" onClick={research} disabled={busy !== null}
            title={st && !st.aiConfigured ? 'Needs an AI model (Settings → AI assistant)' : st && !st.webSearch ? 'Needs web search (a Brave Search key)' : 'The AI searches the web for fairs, expos and meetups — only events with a real source link are kept'}>
            {busy === 'research' ? <><Spinner /> Searching…</> : '🤖 Find more events'}</button>
          <button className="btn" onClick={refresh} disabled={busy !== null} title="Check CSDb's events calendar now">
            {busy === 'refresh' ? <Spinner /> : '↻'} CSDb</button>
          <button className="btn btn-primary" onClick={() => setForm({ ...EMPTY_FORM })} disabled={form !== null}>+ Add event</button>
        </div>
      </header>

      {form && (
        <Card title={form.id ? '✏️ Edit your event' : '+ Add an event'} className="ev-form-card"
          actions={<button className="btn btn-ghost btn-sm" onClick={() => setForm(null)} aria-label="Close">✕</button>}>
          <form className="ev-form" onSubmit={save}>
            <label className="wide">Name<input required maxLength={200} value={form.name} onChange={set('name')} placeholder="C64 club night" /></label>
            <label>Starts<input type="date" required value={form.start} onChange={set('start')} /></label>
            <label>Ends <span className="muted small">(optional)</span><input type="date" min={form.start || undefined} value={form.end ?? ''} onChange={set('end')} /></label>
            <label>City<input maxLength={120} value={form.city ?? ''} onChange={set('city')} /></label>
            <label>Country<input maxLength={80} value={form.country ?? ''} onChange={set('country')} list="ev-countries" /></label>
            <label>Type<input maxLength={120} value={form.type ?? ''} onChange={set('type')} placeholder="Meeting, Demo Party, Computer Fair…" /></label>
            <label>Website<input type="url" maxLength={600} value={form.url ?? ''} onChange={set('url')} placeholder="https://…" /></label>
            <label className="wide">Notes<textarea maxLength={2000} rows={2} value={form.notes ?? ''} onChange={set('notes')} /></label>
            <datalist id="ev-countries">{data?.countries.map((c) => <option key={c} value={c} />)}</datalist>
            <div className="row-actions wide">
              <button className="btn btn-primary" type="submit" disabled={busy === 'save'}>{busy === 'save' ? <Spinner /> : null} {form.id ? 'Save' : 'Add event'}</button>
              <button className="btn btn-ghost" type="button" onClick={() => setForm(null)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}

      {data && data.live.length > 0 && (
        <Card title="🔴 Live now" className="ev-live">
          <ul className="ev-live-list">
            {data.live.map((e) => (
              <li key={e.id}>
                <span className="ev-live-dot" aria-hidden />
                <div className="ev-live-main">
                  <strong>{e.name}</strong>
                  <span className="muted small">{[dateRange(e), e.city, e.country].filter(Boolean).join(' · ')}</span>
                </div>
                <div className="ev-actions">
                  {e.streamUrl && <a className="btn btn-primary btn-sm" href={e.streamUrl} target="_blank" rel="noopener noreferrer">📺 Watch the stream ↗</a>}
                  {e.url && <a className="btn btn-sm" href={e.url} target="_blank" rel="noopener noreferrer">Website ↗</a>}
                  {e.pageUrl && e.source === 'csdb' && <a className="btn btn-ghost btn-sm" href={e.pageUrl} target="_blank" rel="noopener noreferrer">CSDb ↗</a>}
                </div>
              </li>
            ))}
          </ul>
          {data.liveVideos.length > 0 && (
            <>
              <p className="muted small ev-videos-head">🎬 Recent party videos (Transmission64) · <Link to="/news?tab=video">more in 🎬 Watch</Link></p>
              <div className="ev-videos">
                {data.liveVideos.map((v) => (
                  <a key={v.id} className="ev-video" href={v.url} target="_blank" rel="noopener noreferrer" title={v.title}>
                    <span className="thumb">{v.image && <img src={v.image} alt="" loading="lazy" referrerPolicy="no-referrer" />}<span className="play">▶</span></span>
                    <span className="small">{v.title}</span>
                  </a>
                ))}
              </div>
            </>
          )}
        </Card>
      )}

      <div className="ev-filters">
        <div className="ev-filter-row">
          <div className="seg" role="group" aria-label="Which events">
            <button className={`seg-btn ${scope === '' ? 'on' : ''}`} onClick={() => setScope('')}>All retro</button>
            <button className={`seg-btn ${scope === 'commodore' ? 'on' : ''}`} onClick={() => setScope('commodore')}>Commodore & scene</button>
            <button className={`seg-btn ${scope === 'retro' ? 'on' : ''}`} onClick={() => setScope('retro')}>Other retro</button>
          </div>
          <select value={whenId} onChange={(e) => setWhenId(e.target.value)} aria-label="When">
            {WHEN.map((w) => <option key={w.id} value={w.id}>📆 {w.label}</option>)}
          </select>
          {whenId === 'custom' && (
            <>
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From" />
              <span className="muted small">to</span>
              <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} aria-label="To" />
            </>
          )}
        </div>
        <div className="chips">
          {TYPES.map((t) => (
            <button key={t.id} className={`chip ${type === t.id ? 'on' : ''}`} onClick={() => setType(t.id)}>{t.label}</button>
          ))}
        </div>
        <div className="ev-filter-row">
          <select value={country} onChange={(e) => { setCountry(e.target.value); setRegion('') }} aria-label="Country">
            <option value="">🌍 All countries</option>
            {data && <option value="home">⭐ {data.homeCountry} ({data.homeCount})</option>}
            {data?.countries.filter((c) => c !== data.homeCountry).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          {(data?.regions.length ?? 0) > 0 && (country === 'home' || (country && country === data?.regionCountry)) && (
            <select value={region} onChange={(e) => setRegion(e.target.value)} aria-label="State or province">
              <option value="">All states</option>
              {data?.regions.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
          )}
          <input type="search" placeholder="Search events, cities…" value={q} onChange={(e) => setQ(e.target.value)} />
          {!country && <label className="ev-toggle small"><input type="checkbox" checked={homeFirst} onChange={(e) => setHomeFirst(e.target.checked)} /> ⭐ {data?.homeCountry ?? 'Home'} first</label>}
          <label className="ev-toggle small"><input type="checkbox" checked={past} onChange={(e) => setPast(e.target.checked)} /> Past events</label>
          {(data?.hiddenCount ?? 0) > 0 && (
            <label className="ev-toggle small"><input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Hidden ({data?.hiddenCount})</label>
          )}
        </div>
      </div>
      {st && Object.keys(st.errors).length > 0 && (
        <p className="muted small">Couldn't reach {Object.keys(st.errors).join(', ')} last time — it's tried again in a few hours.</p>
      )}

      {homeEvents.length > 0 && (
        <section className="ev-home">
          <h2 className="ev-month-head">⭐ In the {data?.homeCountry} <span className="muted small">({homeEvents.length})</span></h2>
          <ul className="ev-list">{homeEvents.map((e) => <EventRow key={e.id} e={e} busy={busy} onEdit={edit} onRemove={remove} onHide={hide} />)}</ul>
          <h2 className="ev-month-head ev-world-head">🌍 Around the world</h2>
        </section>
      )}
      {!data ? <Spinner /> : data.items.length === 0 ? (
        <Empty>{type || country || q || whenId || scope ? 'No events match — try another filter.' : 'No events yet — press ↻ CSDb, 🤖 Find more events, or add your own.'}</Empty>
      ) : (
        <div className="ev-months">
          {months.map((m) => (
            <section key={m.key} className="ev-month">
              <h2 className="ev-month-head">{monthLabel(m.key)}</h2>
              <ul className="ev-list">
                {m.items.map((e) => <EventRow key={e.id} e={e} busy={busy} onEdit={edit} onRemove={remove} onHide={hide} />)}
              </ul>
            </section>
          ))}
        </div>
      )}

      <Card title="📆 Subscribe" className="ev-subscribe">
        <p className="muted small">Add every upcoming event to your calendar app as a subscription — it stays up to date by itself.
          The link works on this network (and wherever you can reach the console).</p>
        <div className="ev-sub-row">
          <code className="ev-sub-url">{eventsApi.calendarUrl()}</code>
          <button className="btn btn-sm" onClick={copySubscribe}>📋 Copy link</button>
          <a className="btn btn-ghost btn-sm" href="/api/events/calendar.ics" download>⬇ .ics</a>
        </div>
      </Card>
    </div>
  )
}

function EventRow({ e, busy, onEdit, onRemove, onHide }: {
  e: RetroEvent
  busy: unknown
  onEdit: (e: RetroEvent) => void
  onRemove: (e: RetroEvent) => void
  onHide: (e: RetroEvent, hidden: boolean) => void
}) {
  const s = parseDay(e.start)
  const soon = when(e)
  const where = [e.city, e.regionName ? e.region : null, e.country].filter(Boolean).join(', ')
  return (
    <li className={`ev-item ${e.hidden ? 'is-hidden' : ''} ${e.live ? 'is-live' : ''} ${e.home ? 'is-home' : ''}`}>
      <div className="ev-date" aria-label={dateRange(e)}>
        <span className="ev-date-mon">{mon(s)}</span>
        <span className="ev-date-day">{s.getDate()}{e.end !== e.start && <small>–{parseDay(e.end).getDate()}</small>}</span>
      </div>
      <div className="ev-body">
        <div className="ev-title">
          <strong>{e.name}</strong>
          {e.home && <span className="ev-home-badge" title="In your home country">{e.flag || '⭐'} {e.region || e.country}</span>}
          {e.scope === 'commodore' && <span className="ev-scope" title="Commodore / demoscene">C=</span>}
          <span className={`ev-src ev-src-${e.source}`} title={SOURCE_HINT[e.source]}>{e.sourceLabel}</span>
          {e.live && <span className="ev-live-badge">🔴 LIVE</span>}
          {e.hidden && <span className="muted small">(hidden)</span>}
        </div>
        {e.tagline && <div className="ev-tagline">{e.tagline}</div>}
        <div className="muted small">
          {[dateRange(e), soon, (!e.home && e.flag ? `${e.flag} ` : '') + where, e.type].filter(Boolean).join(' · ')}
        </div>
        {e.notes && <div className="small ev-notes">{e.notes}</div>}
        <div className="ev-actions">
          <a className="btn btn-sm" href={e.ics} download title="Download an .ics file for your calendar">📅 Add to calendar</a>
          {e.url && <a className="btn btn-ghost btn-sm" href={e.url} target="_blank" rel="noopener noreferrer">Website ↗</a>}
          {e.streamUrl && <a className="btn btn-ghost btn-sm" href={e.streamUrl} target="_blank" rel="noopener noreferrer">📺 Stream ↗</a>}
          {e.pageUrl && e.pageUrl !== e.url && (
            <a className="btn btn-ghost btn-sm" href={e.pageUrl} target="_blank" rel="noopener noreferrer">{e.source === 'csdb' ? 'CSDb ↗' : 'Source ↗'}</a>
          )}
          {e.editable && <button className="btn btn-ghost btn-sm" onClick={() => onEdit(e)}>✏️ Edit</button>}
          {e.editable && <button className="btn btn-ghost btn-sm" onClick={() => onRemove(e)} disabled={busy === e.id}>🗑 Delete</button>}
          <button className="btn btn-ghost btn-sm" onClick={() => onHide(e, !e.hidden)} disabled={busy === e.id}
            title={e.hidden ? 'Show this event again' : 'Not interested — hide it'}>{e.hidden ? '👁 Show' : '🙈 Hide'}</button>
        </div>
      </div>
    </li>
  )
}
