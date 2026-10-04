import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { errorMessage } from '../services/api'
import { issueDate, magazinesApi } from '../services/magazinesApi'
import type { IssueList, MagazineAnswer, MagazineOverview, MagazineSeries, SearchResult, SnippetPart } from '../services/magazinesApi'
import { Card, Empty, Spinner } from '../components/common'
import { useToast } from '../components/Toasts'
import { MagazineReader } from '../components/MagazineReader'
import type { ReaderTarget } from '../components/MagazineReader'
import './magazines.css'

const EXAMPLES = ['What did Zzap give Paradroid?', 'Who wrote Uridium?', 'What was the Sizzler in the first Zzap?']

export function Highlight({ parts }: { parts: SnippetPart[] }) {
  return <>{parts.map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}</>
}

/** The answer text with its [n] citation marks as small buttons. */
function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  return (
    <p className="mag-answer-text">
      {text.split(/(\[\d+\])/).map((piece, i) => {
        const m = /^\[(\d+)\]$/.exec(piece)
        return m
          ? <button key={i} className="mag-cite" onClick={() => onCite(Number(m[1]))} title={`Excerpt ${m[1]}`}>{m[1]}</button>
          : <span key={i}>{piece}</span>
      })}
    </p>
  )
}

function AskBox({ open, canAsk }: { open: (t: ReaderTarget) => void; canAsk: boolean }) {
  const toast = useToast()
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<MagazineAnswer | null>(null)
  const ask = async (q: string) => {
    if (q.trim().length < 2) return
    setQuestion(q)
    setBusy(true)
    try {
      setAnswer(await magazinesApi.ask(q.trim()))
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  const onCite = (n: number) => {
    const c = answer?.citations.find((x) => x.n === n)
    if (c) open({ title: `${c.seriesName} · ${c.issueTitle}`, readerUrl: c.readerUrl, link: c.link })
  }
  return (
    <Card title="🤖 Ask the magazines">
      <form className="mag-row" onSubmit={(e: FormEvent) => { e.preventDefault(); ask(question) }}>
        <input className="mag-input" type="search" value={question} maxLength={500}
          placeholder="e.g. What did Zzap give Paradroid?" onChange={(e) => setQuestion(e.target.value)} />
        <button className="btn btn-primary" disabled={busy || question.trim().length < 2}>{busy ? <Spinner /> : 'Ask'}</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((q) => <button key={q} className="chip" onClick={() => ask(q)} disabled={busy}>{q}</button>)}
      </div>
      {!canAsk && <p className="muted small">Answers come only from magazines you've made searchable — pick one below and press <strong>Make searchable</strong> first.</p>}
      {answer && (
        <div className="mag-answer">
          <AnswerText text={answer.answer} onCite={onCite} />
          {answer.citations.length > 0 && (
            <ol className="mag-citations">
              {answer.citations.map((c) => (
                <li key={c.n} value={c.n}>
                  <div><strong>{c.seriesName}</strong> · {c.issueTitle}{c.date ? ` · ${issueDate(c.date)}` : ''}</div>
                  <div className="muted small mag-snippet">{c.snippet}</div>
                  <div className="mag-links">
                    <button className="btn btn-ghost btn-sm" onClick={() => onCite(c.n)}>📖 Read</button>
                    <a className="btn btn-ghost btn-sm" href={c.link} target="_blank" rel="noopener noreferrer">open on archive.org ↗</a>
                  </div>
                </li>
              ))}
            </ol>
          )}
          <p className="muted small">🤖 Answered only from the excerpts above (OCR text of the scans — check the page).</p>
        </div>
      )}
    </Card>
  )
}

function SearchBox({ series, open }: { series: MagazineSeries[]; open: (t: ReaderTarget) => void }) {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [only, setOnly] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)
  const run = async (e: FormEvent) => {
    e.preventDefault()
    if (!q.trim()) return
    setBusy(true)
    try {
      setResult(await magazinesApi.search(q.trim(), only || undefined))
    } catch (err) {
      toast(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }
  const searchable = series.filter((s) => s.indexed > 0)
  return (
    <Card title="🔎 Search the pages">
      <form className="mag-row" onSubmit={run}>
        <input className="mag-input" type="search" value={q} maxLength={200} placeholder="A game, a programmer, a company…"
          onChange={(e) => setQ(e.target.value)} />
        <select value={only} onChange={(e) => setOnly(e.target.value)} aria-label="Magazine">
          <option value="">All searchable</option>
          {searchable.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <button className="btn" disabled={busy || !q.trim()}>{busy ? <Spinner /> : 'Search'}</button>
      </form>
      {result && (result.hits.length === 0
        ? <Empty>No pages found for “{result.q}”{searchable.length ? '' : ' — nothing is searchable yet'}.</Empty>
        : (
          <>
            {result.mode === 'any' && <p className="muted small">No page has every word — showing pages with some of them.</p>}
            <ul className="mag-hits">
              {result.hits.map((h) => (
                <li key={`${h.issueKey}:${h.chunk}`}>
                  <div className="mag-hit-head"><strong>{h.seriesName}</strong> · {h.issueTitle}
                    {h.date && <span className="muted"> · {issueDate(h.date)}</span>}</div>
                  <div className="mag-snippet small"><Highlight parts={h.parts} /></div>
                  <div className="mag-links">
                    <button className="btn btn-ghost btn-sm" onClick={() => open({ title: `${h.seriesName} · ${h.issueTitle}`, readerUrl: h.readerUrl, link: h.link })}>📖 Read</button>
                    <a className="btn btn-ghost btn-sm" href={h.link} target="_blank" rel="noopener noreferrer">find “{result.q}” on archive.org ↗</a>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ))}
    </Card>
  )
}

/** A cover from the console's cache (fetched from the Internet Archive the first time); a plain tile if there's none. */
function Cover({ src, label }: { src: string; label: string }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className="mag-cover">
      {failed ? <span className="mag-cover-none">{label}</span>
        : <img src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />}
    </span>
  )
}

function SeriesCard({ s, onBrowse, onChanged, browsing }: {
  s: MagazineSeries; onBrowse: () => void; onChanged: () => void; browsing: boolean
}) {
  const toast = useToast()
  const job = s.job
  const all = s.issueCount > 0 && s.indexed >= s.issueCount
  const start = async () => {
    try {
      await magazinesApi.startIndex(s.id)
      toast(`📚 Making ${s.name} searchable — one issue at a time`, 'info')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
    onChanged()
  }
  const cancel = async () => {
    try {
      await magazinesApi.cancelIndex(s.id)
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
    onChanged()
  }
  const pct = job && job.total ? Math.round((100 * job.done) / job.total) : 0
  return (
    <section className={`mag-series${browsing ? ' on' : ''}`}>
      {s.coverUrl && (
        <button className="mag-series-cover" onClick={onBrowse} title={`Browse ${s.name}`} aria-label={`Browse ${s.name}`}>
          <Cover src={s.coverUrl} label={s.name} />
        </button>
      )}
      <h3>{s.name}</h3>
      <p className="muted small">{s.about}</p>
      <p className="small">
        {s.issueCount} issues · {all ? '✓ all searchable' : `${s.indexed} searchable`}
      </p>
      {s.indexing && job && (
        <div className="mag-progress" aria-live="polite">
          <div className="mag-bar"><span style={{ width: `${pct}%` }} /></div>
          <div className="muted small">
            {job.queued ? 'Waiting for another magazine to finish…' : `${job.done}/${job.total}${job.current ? ` · ${job.current}` : ''}`}
          </div>
        </div>
      )}
      {!s.indexing && job && job.finishedAt && (job.errors > 0 || job.skipped > 0 || job.cancelled) && (
        <p className="muted small">
          {job.cancelled ? 'Stopped. ' : ''}{job.skipped ? `${job.skipped} skipped (no text / too large). ` : ''}
          {job.errors ? `${job.errors} failed — try again later.` : ''}
        </p>
      )}
      {s.error && s.issueCount === 0 && <p className="muted small">Couldn't reach the Internet Archive: {s.error}</p>}
      <div className="mag-links">
        {s.indexing
          ? <button className="btn btn-sm" onClick={cancel}>Cancel</button>
          : <button className="btn btn-primary btn-sm" onClick={start} disabled={all || s.issueCount === 0}>{s.indexed ? 'Make the rest searchable' : 'Make searchable'}</button>}
        <button className="btn btn-ghost btn-sm" onClick={onBrowse}>{browsing ? 'Hide issues' : 'Browse issues'}</button>
      </div>
    </section>
  )
}

function IssueGrid({ series, open, refreshKey }: { series: string; open: (t: ReaderTarget) => void; refreshKey: number }) {
  const [list, setList] = useState<IssueList | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    magazinesApi.issues(series).then((l) => { if (live) { setList(l); setError(null) } })
      .catch((e) => { if (live) setError(errorMessage(e)) })
    return () => { live = false }
  }, [series, refreshKey])
  if (error) return <Card><Empty>{error}</Empty></Card>
  if (!list || list.series.id !== series) return <Card><Empty><Spinner /></Empty></Card>
  return (
    <Card title={`📚 ${list.series.name} · ${list.issues.length} issues`}>
      {list.issues.length === 0 ? <Empty>No issues found{list.error ? ` (${list.error})` : ''}.</Empty> : (
        <div className="mag-issues">
          {list.issues.map((i) => (
            <button key={i.key} className="mag-issue" onClick={() => open({ title: i.title, readerUrl: i.readerUrl, link: i.detailsUrl })}
              title={`Read ${i.title}`}>
              <Cover src={i.coverUrl} label={i.number != null ? `#${i.number}` : i.title} />
              <span className="mag-issue-meta">
                <span className="mag-issue-num">{i.number ?? '·'}</span>
                <span className="mag-issue-date small">{issueDate(i.date) || '—'}</span>
              </span>
              {i.indexed && <span className="mag-issue-ok small" title="Searchable">🔎</span>}
            </button>
          ))}
        </div>
      )}
    </Card>
  )
}

/** 📚 Classic C64 magazines from the Internet Archive — read, search and ask them. */
export function MagazinesPage() {
  const toast = useToast()
  const [data, setData] = useState<MagazineOverview | null>(null)
  const [browse, setBrowse] = useState<string | null>(null)
  const [reader, setReader] = useState<ReaderTarget | null>(null)
  const [tick, setTick] = useState(0)
  const load = useCallback(() => {
    magazinesApi.overview().then(setData).catch((e) => toast(errorMessage(e), 'error'))
  }, [toast])
  useEffect(() => { load() }, [load])
  const indexing = !!data?.series.some((s) => s.indexing)
  useEffect(() => {
    if (!indexing) return
    const t = window.setInterval(() => { load(); setTick((n) => n + 1) }, 2500)
    return () => window.clearInterval(t)
  }, [indexing, load])
  const closeReader = useCallback(() => setReader(null), [])
  return (
    <div className="page mag-page">
      <header className="page-head">
        <div>
          <h1>📚 Magazine archive</h1>
          <p className="muted">Zzap!64, Compute!'s Gazette, Commodore Format and more — read the scans, search every page, ask what the reviewers said.</p>
        </div>
      </header>

      <AskBox open={setReader} canAsk={(data?.indexedTotal ?? 0) > 0} />
      <SearchBox series={data?.series ?? []} open={setReader} />

      {!data ? <Card><Empty><Spinner /> Looking up the magazines on the Internet Archive…</Empty></Card> : (
        <div className="mag-series-grid">
          {data.series.map((s) => (
            <SeriesCard key={s.id} s={s} browsing={browse === s.id} onChanged={load}
              onBrowse={() => setBrowse(browse === s.id ? null : s.id)} />
          ))}
        </div>
      )}
      {data && !data.fts && <p className="muted small">This system's SQLite has no full-text search (FTS5) — a simpler search is used.</p>}

      {browse && <IssueGrid series={browse} open={setReader} refreshKey={tick} />}

      <p className="muted small mag-note">
        © The magazines belong to their publishers. The scans are hosted by the <a href="https://archive.org" target="_blank" rel="noopener noreferrer">Internet Archive</a>;
        this app links to and embeds its reader rather than copying them. "Make searchable" keeps only the OCR text, on this console, for searching.
      </p>
      <MagazineReader target={reader} onClose={closeReader} />
    </div>
  )
}
