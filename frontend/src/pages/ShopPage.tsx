import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { api, errorMessage } from '../services/api'
import { hobbyApi, money } from '../services/hobbyApi'
import type { Catalog, Listing, ShopItem, Suggestion, Watch } from '../services/hobbyApi'
import { ProductGrid } from './NewsPage'
import type { NewsItem } from './NewsPage'
import './hobby.css'

type Tab = 'new' | 'arrivals' | 'used' | 'kit' | 'setup'
const TABS: { id: Tab; label: string }[] = [
  { id: 'new', label: '🧰 Catalog' }, { id: 'arrivals', label: '🆕 New arrivals' }, { id: 'used', label: '🔁 Used & rare' }, { id: 'kit', label: '🤖 Starter kit' }, { id: 'setup', label: '🧭 For my setup' },
]
const USED_CHIPS = ['Commodore 64 breadbin', 'Commodore 64C', 'Commodore 1541 disk drive', 'Commodore 1702 monitor', 'C64 boxed game', 'C64 cartridge', 'Commodore 128', 'SX-64']

const CATEGORY_ICON: Record<string, string> = {
  power: '⚡', storage: '💾', 'cartridges-memory': '🧩', video: '📺', input: '🕹', sound: '🎵', 'repair-parts': '🔧',
  diagnostics: '🩺', accessories: '🎒', kits: '📦',
}

/** One catalog item with its photo and seller links (🔗 = affiliate link). */
export function ShopItemCard({ item, compact = false }: { item: ShopItem; compact?: boolean }) {
  const [broken, setBroken] = useState(false)
  return (
    <div className={`shop-item ${compact ? 'compact' : ''}`}>
      <a className="shop-photo" href={item.links[0]?.url} target="_blank" rel="noopener noreferrer sponsored" tabIndex={-1} aria-hidden>
        {item.imageUrl && !broken
          ? <img src={item.imageUrl} alt="" loading="lazy" onError={() => setBroken(true)} />
          : <span>{CATEGORY_ICON[item.category] ?? '🖥'}</span>}
      </a>
      <strong>{item.name}</strong>
      {!compact && <span className="muted small">{item.description}</span>}
      <div className="shop-links">
        {item.links.map((ln) => (
          <a key={ln.url} className="btn btn-sm" href={ln.url} target="_blank" rel="noopener noreferrer sponsored"
            title={`${ln.sellerName}${ln.affiliate ? ' — affiliate link' : ''} (opens their shop)`}>
            {ln.price ? `${money(ln.price, ln.currency)} · ` : ''}{ln.sellerName} {ln.affiliate ? '🔗' : ''}↗</a>
        ))}
      </div>
    </div>
  )
}

export function SuggestionList({ suggestions }: { suggestions: Suggestion[] }) {
  if (!suggestions.length) return null
  return (
    <div className="suggestions">
      {suggestions.map((s) => (
        <div key={s.why} className="suggestion">
          <p><strong>{s.why}</strong></p>
          <div className="shop-grid">{s.items.slice(0, 4).map((i) => <ShopItemCard key={i.id} item={i} compact />)}</div>
        </div>
      ))}
    </div>
  )
}

/** 🛒 Hardware: a curated catalog that sends you to the sellers, used gear on eBay, and an AI starter-kit advisor. */
export function ShopPage() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'new'
  const [cat, setCat] = useState<Catalog | null>(null)
  const [category, setCategory] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => { hobbyApi.catalog().then(setCat).catch((e) => toast(errorMessage(e), 'error')) }, [toast])
  const items = (cat?.items ?? []).filter((i) => (!category || i.category === category)
    && (!q || `${i.name} ${i.description} ${i.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase())))
  return (
    <div className="page shop-page">
      <header className="page-head">
        <div>
          <h1>🛒 Hardware</h1>
          <p className="muted">Gear for your Commodore from the makers who build it — you buy on their sites.</p>
        </div>
      </header>
      <div className="seg" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`seg-btn ${tab === t.id ? 'on' : ''}`}
            onClick={() => setParams((p) => { const n = new URLSearchParams(p); n.set('tab', t.id); return n })}>{t.label}</button>
        ))}
      </div>

      {tab === 'new' && (!cat ? <Spinner /> : (
        <>
          <div className="news-bar">
            <div className="chips">
              <button className={`chip ${!category ? 'on' : ''}`} onClick={() => setCategory('')}>All</button>
              {Object.entries(cat.categories).filter(([id]) => cat.items.some((i) => i.category === id)).map(([id, label]) => (
                <button key={id} className={`chip ${category === id ? 'on' : ''}`} onClick={() => setCategory(id)}>{label}</button>
              ))}
            </div>
            <input type="search" className="news-search" placeholder="Search gear…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {items.length ? <div className="shop-grid">{items.map((i) => <ShopItemCard key={i.id} item={i} />)}</div> : <Empty>Nothing matches.</Empty>}
        </>
      ))}
      {tab === 'arrivals' && <NewArrivals />}
      {tab === 'used' && <UsedGear />}
      {tab === 'kit' && <StarterKit />}
      {tab === 'setup' && <ForMySetup />}

      {cat && <p className="muted small disclosure">ℹ {cat.disclosure} {cat.source === 'website' ? `Catalog from your website (updated ${cat.updated}).` : `Built-in catalog (${cat.updated}).`}</p>}
    </div>
  )
}

function NewArrivals() {
  const [items, setItems] = useState<NewsItem[] | null>(null)
  useEffect(() => { api.news({ kind: 'product', limit: 120 }).then((r) => setItems(r.items)).catch(() => setItems([])) }, [])
  if (!items) return <Spinner />
  return (
    <>
      <p className="muted small">New products spotted in the makers' shops — checked daily. <a href="/settings#updates">🔄 Sources & updates</a></p>
      {items.length ? <ProductGrid items={items} /> : <Empty>Nothing new yet — the shops are checked daily (the first look only notes what they already sell).</Empty>}
    </>
  )
}

function UsedGear() {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [max, setMax] = useState('')
  const [res, setRes] = useState<{ configured: boolean; searchUrl: string; soldUrl: string; items: Listing[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [w, setW] = useState<{ watches: Watch[]; deals: NewsItem[]; ebayConfigured: boolean } | null>(null)
  const loadW = () => hobbyApi.watches().then(setW).catch(() => {})
  useEffect(() => { loadW() }, [])
  const search = async (query = q) => {
    if (query.trim().length < 2) return
    setQ(query)
    setBusy(true)
    try { setRes(await hobbyApi.ebay(query.trim(), max ? Number(max) : undefined)) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const watch = async () => {
    try { await hobbyApi.addWatch(q.trim(), max ? Number(max) : null); toast('💰 Watching — deals show up in What\'s new', 'ok'); loadW() } catch (e) { toast(errorMessage(e), 'error') }
  }
  return (
    <>
      <Card title="🔁 Used & rare on eBay">
        <form className="row-form" onSubmit={(e) => { e.preventDefault(); search() }}>
          <input type="search" placeholder="e.g. Commodore 1541 disk drive" value={q} onChange={(e) => setQ(e.target.value)} />
          <input type="number" min="1" placeholder="Max price" value={max} onChange={(e) => setMax(e.target.value)} className="price-input" />
          <button className="btn btn-primary" disabled={busy}>{busy ? <Spinner /> : 'Search'}</button>
          <button type="button" className="btn btn-ghost" disabled={q.trim().length < 2} onClick={watch} title="Get a 💰 alert when a listing at or under your price appears">💰 Watch</button>
        </form>
        <div className="chips">{USED_CHIPS.map((c) => <button key={c} className="chip" onClick={() => search(c)}>{c}</button>)}</div>
        {res && (
          <>
            {!res.configured && <p className="muted small">Live listings need eBay developer keys (Settings → Shop). Meanwhile: <a href={res.searchUrl} target="_blank" rel="noopener noreferrer">search eBay ↗</a> · <a href={res.soldUrl} target="_blank" rel="noopener noreferrer">recently sold ↗</a></p>}
            {res.items.length > 0 && (
              <div className="listing-grid">
                {res.items.map((l) => (
                  <a key={l.id} className="listing" href={l.url} target="_blank" rel="noopener noreferrer sponsored">
                    {l.image && <img src={l.image} alt="" loading="lazy" referrerPolicy="no-referrer" />}
                    <span className="small">{l.title}</span>
                    <strong>{money(l.price, l.currency)}</strong>
                    <span className="muted small">{[l.condition, l.location].filter(Boolean).join(' · ')}{l.affiliate ? ' · 🔗' : ''}</span>
                  </a>
                ))}
              </div>
            )}
            {res.configured && <p className="muted small"><a href={res.soldUrl} target="_blank" rel="noopener noreferrer">What these really sold for (eBay sold listings) ↗</a></p>}
          </>
        )}
      </Card>
      {w && (w.watches.length > 0 || w.deals.length > 0) && (
        <Card title="💰 Price watches">
          {w.watches.map((x) => (
            <div key={x.id} className="watch-row">
              <span>{x.query}{x.maxPrice ? ` ≤ ${money(x.maxPrice, x.currency)}` : ''}</span>
              <span className="muted small">{x.lowest ? `lowest seen ${money(x.lowest, x.currency)}` : w.ebayConfigured ? 'not checked yet' : 'needs eBay keys'}</span>
              <button className="btn btn-ghost btn-sm" onClick={() => hobbyApi.removeWatch(x.id).then(loadW)}>✕</button>
            </div>
          ))}
          {w.deals.map((d) => <p key={d.id} className="small"><a href={d.url} target="_blank" rel="noopener noreferrer sponsored">{d.title} ↗</a> <span className="muted">{d.summary}</span></p>)}
        </Card>
      )}
    </>
  )
}

function StarterKit() {
  const toast = useToast()
  const [prompt, setPrompt] = useState('I just got a C64 Ultimate — what else do I need?')
  const [budget, setBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [kit, setKit] = useState<Awaited<ReturnType<typeof hobbyApi.advisor>> | null>(null)
  const go = async () => {
    setBusy(true)
    try { setKit(await hobbyApi.advisor(prompt, budget ? Number(budget) : undefined)) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  return (
    <Card title="🤖 Starter-kit advisor">
      <form className="row-form" onSubmit={(e) => { e.preventDefault(); go() }}>
        <input value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={400} />
        <input type="number" min="1" placeholder="Budget" value={budget} onChange={(e) => setBudget(e.target.value)} className="price-input" />
        <button className="btn btn-primary" disabled={busy}>{busy ? '🤖 Thinking…' : 'Build my kit'}</button>
      </form>
      <div className="chips">
        {['I just got a C64 Ultimate — what else do I need?', 'I found my old breadbin C64 in the attic', 'Best way to play my old floppy disks', 'Gift ideas for a C64 fan'].map((p) => (
          <button key={p} className="chip" onClick={() => setPrompt(p)}>{p}</button>
        ))}
      </div>
      {kit && (
        <div className="kit">
          <p>{kit.intro}</p>
          {kit.kit.map((k) => (
            <div key={k.item.id} className="kit-row">
              <span className={`badge-prio ${k.priority}`}>{k.priority === 'must' ? 'Must have' : 'Nice to have'}</span>
              <div><ShopItemCard item={k.item} compact /><span className="muted small">{k.why}</span></div>
            </div>
          ))}
          {kit.tips.length > 0 && <ul className="small">{kit.tips.map((t) => <li key={t}>{t}</li>)}</ul>}
        </div>
      )}
    </Card>
  )
}

function ForMySetup() {
  const [s, setS] = useState<Suggestion[] | null>(null)
  useEffect(() => {
    Promise.all([hobbyApi.suggest({ context: 'setup' }), hobbyApi.suggest({ context: 'controller' })])
      .then(([a, b]) => setS([...a.suggestions, ...b.suggestions])).catch(() => setS([]))
  }, [])
  if (!s) return <Spinner />
  return s.length ? <SuggestionList suggestions={s} /> : <Empty>No suggestions right now.</Empty>
}
