import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'
import { hobbyApi, money } from '../services/hobbyApi'
import type { CollectionItem, CollectionList, ItemInput } from '../services/hobbyApi'
import './hobby.css'

type View = 'owned' | 'wishlist'

/** 📦 Your physical collection: what you own, what it's worth, what you want — with an insurance export. */
export function CollectionPage() {
  const toast = useToast()
  const [view, setView] = useState<View>('owned')
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const [data, setData] = useState<CollectionList | null>(null)
  const [editing, setEditing] = useState<CollectionItem | 'new' | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const load = useCallback(() => {
    hobbyApi.collection({ kind: kind || undefined, q: q || undefined, wishlist: view === 'wishlist' })
      .then(setData).catch((e) => toast(errorMessage(e), 'error'))
  }, [kind, q, view, toast])
  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t) }, [load, q])

  const estimate = async (it: CollectionItem) => {
    setBusy(it.id)
    try {
      const r = await hobbyApi.estimate(it.id)
      if (r.value !== null) toast(`💲 ${it.title}: about ${money(r.value, r.currency)} (${r.basis})`, 'ok')
      else if (!r.configured) { toast('Live prices need eBay keys (Settings → Shop) — opening eBay sold listings instead', 'ok'); window.open(r.soldUrl, '_blank', 'noopener') }
      else toast('No current listings found — check the sold listings', 'error')
      load()
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(null) }
  }
  const remove = async (it: CollectionItem) => {
    if (!window.confirm(`Remove “${it.title}” from your collection?`)) return
    await hobbyApi.deleteItem(it.id).catch((e) => toast(errorMessage(e), 'error'))
    load()
  }
  const s = data?.summary
  return (
    <div className="page collection-page">
      <header className="page-head">
        <div>
          <h1>📦 Collection</h1>
          <p className="muted">{s ? `${s.owned} item${s.owned === 1 ? '' : 's'} · ${Object.entries(s.totals).map(([c, v]) => money(v, c)).join(' + ') || 'no values yet'}` : ' '}</p>
        </div>
        <div className="row-actions">
          <button className="btn btn-primary" onClick={() => setEditing('new')}>+ Add</button>
          <a className="btn btn-ghost" href="/api/collection/export.csv" download>⬇ CSV</a>
          <a className="btn btn-ghost" href="/api/collection/report" target="_blank" rel="noopener" title="Printable inventory with values — for insurance">🖨 Insurance report</a>
        </div>
      </header>
      <div className="news-bar">
        <div className="seg">
          <button className={`seg-btn ${view === 'owned' ? 'on' : ''}`} onClick={() => setView('owned')}>📦 I own</button>
          <button className={`seg-btn ${view === 'wishlist' ? 'on' : ''}`} onClick={() => setView('wishlist')}>⭐ Wishlist</button>
        </div>
        {data && (
          <div className="chips">
            <button className={`chip ${!kind ? 'on' : ''}`} onClick={() => setKind('')}>All</button>
            {Object.entries(data.kinds).map(([id, label]) => <button key={id} className={`chip ${kind === id ? 'on' : ''}`} onClick={() => setKind(id)}>{label}</button>)}
          </div>
        )}
        <input type="search" className="news-search" placeholder="Search your collection…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {view === 'wishlist' && <p className="muted small">Set a target price and the console watches eBay for you — a listing at or under it shows up as 💰 in What's new (needs eBay keys in Settings).</p>}

      {!data ? <Spinner /> : data.items.length === 0 ? (
        <Empty>{view === 'owned' ? 'Nothing logged yet — press + Add. Games you own are matched to your library for box art.' : 'Your wishlist is empty.'}</Empty>
      ) : (
        <div className="collection-grid">
          {data.items.map((it) => (
            <div key={it.id} className="collection-card">
              <div className="thumb">{it.coverUrl ? <img src={it.coverUrl} alt="" loading="lazy" /> : <span>{(data.kinds[it.kind] ?? '📦').split(' ')[0]}</span>}</div>
              <div className="body">
                <strong>{it.title}</strong>{it.quantity > 1 && <span className="muted"> ×{it.quantity}</span>}
                <span className="muted small">{[data.kinds[it.kind]?.split(' ').slice(1).join(' '), it.edition, it.condition && data.conditions[it.condition],
                  it.boxed && 'boxed', it.complete && 'complete', it.location && `📍 ${it.location}`].filter(Boolean).join(' · ')}</span>
                {it.wishlist
                  ? <span className="small">{it.targetPrice ? `💰 watching for ≤ ${money(it.targetPrice, it.currency)}` : 'Set a target price to get alerts'}</span>
                  : <span className="small">{it.value !== null ? <b>{money(it.value * it.quantity, it.currency)}</b> : <span className="muted">no value yet</span>}
                    {it.valueSource && <span className="muted"> · {it.valueSource}{it.valueAt ? `, ${it.valueAt.slice(0, 10)}` : ''}</span>}</span>}
                <div className="row-actions">
                  {!it.wishlist && <button className="btn btn-ghost btn-sm" disabled={busy === it.id} onClick={() => estimate(it)} title="Value guide from current eBay prices">{busy === it.id ? <Spinner /> : '💲 Value'}</button>}
                  {it.gameId && <Link className="btn btn-ghost btn-sm" to={`/games/${it.gameId}`} title="In your library">▤</Link>}
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(it)}>✎</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => remove(it)} aria-label="Remove">🗑</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {editing && data && (
        <ItemForm item={editing === 'new' ? null : editing} wishlist={view === 'wishlist'} kinds={data.kinds} conditions={data.conditions}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
      )}
    </div>
  )
}

function ItemForm({ item, wishlist, kinds, conditions, onClose, onSaved }: {
  item: CollectionItem | null; wishlist: boolean; kinds: Record<string, string>; conditions: Record<string, string>
  onClose: () => void; onSaved: () => void
}) {
  const toast = useToast()
  const [f, setF] = useState<ItemInput>(() => item ? {
    kind: item.kind, title: item.title, edition: item.edition, condition: item.condition, boxed: item.boxed, complete: item.complete,
    quantity: item.quantity, serial: item.serial, notes: item.notes, location: item.location, purchase_price: item.purchasePrice,
    purchase_date: item.purchaseDate, value: item.value, currency: item.currency, wishlist: item.wishlist, target_price: item.targetPrice,
  } : { kind: 'game', title: '', condition: 'good', quantity: 1, currency: 'USD', wishlist })
  const [titles, setTitles] = useState<{ id: number; title: string }[]>([])
  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((f.title ?? '').length < 2) return setTitles([])
      api.library({ q: f.title, limit: 8 }).then((r) => setTitles(r.items.map((g) => ({ id: g.id, title: g.title })))).catch(() => {})
    }, 250)
    return () => window.clearTimeout(t)
  }, [f.title])
  const set = <K extends keyof ItemInput>(k: K, v: ItemInput[K]) => setF((o) => ({ ...o, [k]: v }))
  const num = (v: string) => (v === '' ? null : Number(v))
  const save = async () => {
    try {
      const match = titles.find((t) => t.title.toLowerCase() === (f.title ?? '').toLowerCase())
      const data = { ...f, game_id: item?.gameId ?? match?.id ?? null }
      if (item) await hobbyApi.updateItem(item.id, data)
      else await hobbyApi.addItem(data)
      onSaved()
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  return (
    <div className="video-overlay" role="dialog" aria-label="Collection item" onClick={onClose}>
      <form className="video-box item-form" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); save() }}>
        <header><strong>{item ? 'Edit item' : f.wishlist ? 'Add to wishlist' : 'Add to collection'}</strong>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>✕</button></header>
        <div className="form-grid">
          <label>Kind<select value={f.kind} onChange={(e) => set('kind', e.target.value)}>{Object.entries(kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="wide">Title<input required list="lib-titles" value={f.title ?? ''} onChange={(e) => set('title', e.target.value)} maxLength={200} autoFocus />
            <datalist id="lib-titles">{titles.map((t) => <option key={t.id} value={t.title} />)}</datalist></label>
          <label className="wide">Edition / publisher / region<input value={f.edition ?? ''} onChange={(e) => set('edition', e.target.value || null)} maxLength={120} /></label>
          <label>Condition<select value={f.condition ?? ''} onChange={(e) => set('condition', e.target.value || null)}>
            <option value="">—</option>{Object.entries(conditions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>Quantity<input type="number" min={1} value={f.quantity ?? 1} onChange={(e) => set('quantity', Number(e.target.value) || 1)} /></label>
          <label className="check"><input type="checkbox" checked={!!f.boxed} onChange={(e) => set('boxed', e.target.checked)} /> Boxed</label>
          <label className="check"><input type="checkbox" checked={!!f.complete} onChange={(e) => set('complete', e.target.checked)} /> Complete (manual, inlays)</label>
          <label className="check wide"><input type="checkbox" checked={!!f.wishlist} onChange={(e) => set('wishlist', e.target.checked)} /> ⭐ On my wishlist (I don't have it yet)</label>
          {f.wishlist ? (
            <label>Target price<input type="number" min={0} step="0.01" value={f.target_price ?? ''} onChange={(e) => set('target_price', num(e.target.value))} /></label>
          ) : (
            <>
              <label>Value (each)<input type="number" min={0} step="0.01" value={f.value ?? ''} onChange={(e) => set('value', num(e.target.value))} /></label>
              <label>Paid<input type="number" min={0} step="0.01" value={f.purchase_price ?? ''} onChange={(e) => set('purchase_price', num(e.target.value))} /></label>
              <label>Bought (YYYY-MM-DD)<input value={f.purchase_date ?? ''} pattern="\d{4}(-\d{2}(-\d{2})?)?" onChange={(e) => set('purchase_date', e.target.value || null)} /></label>
              <label>Serial number<input value={f.serial ?? ''} onChange={(e) => set('serial', e.target.value || null)} maxLength={80} /></label>
              <label>Stored at<input value={f.location ?? ''} onChange={(e) => set('location', e.target.value || null)} maxLength={80} placeholder="shelf, box…" /></label>
            </>
          )}
          <label>Currency<input value={f.currency ?? 'USD'} onChange={(e) => set('currency', e.target.value.toUpperCase())} maxLength={3} /></label>
          <label className="wide">Notes<textarea value={f.notes ?? ''} onChange={(e) => set('notes', e.target.value || null)} maxLength={2000} rows={2} /></label>
        </div>
        <div className="row-actions"><button className="btn btn-primary">Save</button></div>
      </form>
    </div>
  )
}
