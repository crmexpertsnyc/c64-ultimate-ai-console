import { useEffect, useState } from 'react'
import { Card, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { errorMessage } from '../services/api'
import { hobbyApi } from '../services/hobbyApi'
import type { Diagnosis, Machine } from '../services/hobbyApi'
import { VideoPlayer } from './NewsPage'
import type { NewsItem } from './NewsPage'
import { ShopItemCard } from './ShopPage'
import './hobby.css'

const ICON: Record<string, string> = { breadbin: '🍞', c64c: '⌨', sx64: '🧳', 'c64-ultimate': '✨', '1541': '💾' }
const LIKELY: Record<string, string> = { high: 'Most likely', medium: 'Possible', low: 'Less likely' }

/** 🔧 Repair assistant: describe the symptom → known causes, checks, safe parts, manuals and videos. */
export function RepairPage() {
  const toast = useToast()
  const [kb, setKb] = useState<Awaited<ReturnType<typeof hobbyApi.repairKb>> | null>(null)
  const [machine, setMachine] = useState('breadbin')
  const [text, setText] = useState('')
  const [symptom, setSymptom] = useState<string | undefined>()
  const [busy, setBusy] = useState(false)
  const [d, setD] = useState<Diagnosis | null>(null)
  const [watching, setWatching] = useState<NewsItem | null>(null)
  useEffect(() => { hobbyApi.repairKb().then(setKb).catch((e) => toast(errorMessage(e), 'error')) }, [toast])
  const run = async (sym?: string) => {
    setBusy(true)
    setSymptom(sym)
    try { setD(await hobbyApi.diagnose(machine, text, sym)) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const m: Machine | undefined = kb?.machines.find((x) => x.id === machine)
  return (
    <div className="page repair-page">
      <header className="page-head">
        <div>
          <h1>🔧 Repair assistant</h1>
          <p className="muted">Describe what your machine does — get the usual causes, what to check, parts, manuals and videos.</p>
        </div>
      </header>
      {!kb ? <Spinner /> : (
        <Card title="1. Which machine?">
          <div className="seg machine-seg" role="radiogroup">
            {kb.machines.map((x) => (
              <button key={x.id} role="radio" aria-checked={machine === x.id} className={`seg-btn ${machine === x.id ? 'on' : ''}`}
                onClick={() => { setMachine(x.id); setD(null) }}>{ICON[x.id] ?? '🖥'} {x.name}</button>
            ))}
          </div>
          {m && <p className="muted small">{m.note}</p>}
          <h3>2. What happens?</h3>
          <form className="row-form" onSubmit={(e) => { e.preventDefault(); run() }}>
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={600}
              placeholder={machine === 'c64-ultimate' ? 'e.g. no picture on my TV over HDMI' : 'e.g. black screen, the power light is on'} />
            <button className="btn btn-primary" disabled={busy || !text.trim()}>{busy ? <Spinner /> : 'Diagnose'}</button>
          </form>
          <div className="chips">
            {kb.symptoms.filter((s) => s.machines.includes(machine)).map((s) => (
              <button key={s.id} className={`chip ${symptom === s.id ? 'on' : ''}`} onClick={() => run(s.id)}>{s.title}</button>
            ))}
          </div>
        </Card>
      )}

      {d && (
        <>
          {d.safety.map((s) => <div key={s.id} className="safety-box">⚠ {s.text}</div>)}
          <Card title={d.symptoms.length ? `🩺 ${d.symptoms.join(' · ')}` : '🩺 Diagnosis'}>
            {d.summary && <p>{d.summary}</p>}
            {!d.ai && <p className="muted small">{d.aiError ? `AI not available (${d.aiError}) — ` : ''}Matched from the repair knowledge base{d.aiError ? '' : '; set up an AI model for answers tailored to your description'}.</p>}
            <ol className="causes">
              {d.causes.map((c) => (
                <li key={c.ref}>
                  <span className={`badge-likely ${c.likelihood}`}>{LIKELY[c.likelihood]}</span> <strong>{c.title}</strong>
                  <p className="small">{c.aiWhy || c.why}</p>
                  {c.checks.length > 0 && <ul className="small checks">{c.checks.map((k) => <li key={k}>☐ {k}</li>)}</ul>}
                </li>
              ))}
            </ol>
            {d.questions.length > 0 && <><h4>To narrow it down</h4><ul className="small">{d.questions.map((q) => <li key={q}>{q}</li>)}</ul></>}
            {d.extraChecks.length > 0 && <><h4>Quick checks</h4><ul className="small">{d.extraChecks.map((q) => <li key={q}>☐ {q}</li>)}</ul></>}
          </Card>
          {d.parts.length > 0 && (
            <Card title="🛒 Parts & tools">
              <div className="shop-grid">{d.parts.map((p) => <ShopItemCard key={p.id} item={p} compact />)}</div>
              <p className="muted small">Links go to the sellers' shops.{' '}
                <a href={`https://www.google.com/search?q=${encodeURIComponent('Commodore 64 repair service near me')}`} target="_blank" rel="noopener noreferrer">Rather have it repaired? Find a technician ↗</a>
                {d.services.map((s) => <span key={s.url}> · <a href={s.url} target="_blank" rel="noopener noreferrer">{s.name} ↗</a></span>)}</p>
            </Card>
          )}
          <div className="grid-2">
            <Card title="📘 Manuals & guides">
              <ul className="ref-list">{d.references.map((r) => <li key={r.url}><a href={r.url} target="_blank" rel="noopener noreferrer">{r.title} ↗</a></li>)}</ul>
            </Card>
            <Card title="🎬 Repair videos">
              {d.videos.items.length > 0 ? (
                <div className="mini-videos">
                  {d.videos.items.map((v) => (
                    <button key={v.id} className="mini-video" onClick={() => setWatching(v)}>
                      {v.image && <img src={v.image} alt="" loading="lazy" referrerPolicy="no-referrer" />}
                      <span className="small">{v.title}</span><span className="muted small">{v.author}</span>
                    </button>
                  ))}
                </div>
              ) : <p className="muted small">No matching videos from your channels yet.</p>}
              <a className="btn btn-ghost btn-sm" href={d.videos.searchUrl} target="_blank" rel="noopener noreferrer">Search YouTube for “{d.videos.query}” ↗</a>
            </Card>
          </div>
        </>
      )}
      {watching && <VideoPlayer item={watching} onClose={() => setWatching(null)} />}
    </div>
  )
}
