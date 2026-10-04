import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { CatalogItem } from '../shared/types'
import { PlayChoice } from './PlayChoice'
import { ElsewhereLinks } from './Elsewhere'
import type { ElsewhereResult } from './Elsewhere'
import { useToast } from './Toasts'

export interface AskGame {
  title: string
  gameId: number | null
  catalog: CatalogItem | null
  browserOk: boolean
  coverUrl?: string | null
  elsewhere?: ElsewhereResult[]
}

export interface AskResult {
  question: string
  answer: string
  games: AskGame[]
  sources: { n: number; title: string; url: string }[]
  webSearch: boolean
  searchNote: string | null
  model: string | null
}

// Inline formatting from the model's plain text: **bold** and [n] source references. Rendered as React
// nodes — the model's text is never inserted as HTML.
function inline(text: string, sources: AskResult['sources']): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\[\d{1,2}\])/g)
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>
    const m = p.match(/^\[(\d{1,2})\]$/)
    if (m) {
      const src = sources.find((s) => s.n === Number(m[1]))
      return src
        ? <a key={i} className="cite" href={src.url} target="_blank" rel="noreferrer" title={src.title}>[{m[1]}]</a>
        : <sup key={i} className="muted">[{m[1]}]</sup>
    }
    return p
  })
}

function Formatted({ text, sources }: { text: string; sources: AskResult['sources'] }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  const flush = () => {
    if (bullets.length) blocks.push(<ul key={`u${blocks.length}`}>{bullets.map((b, i) => <li key={i}>{inline(b, sources)}</li>)}</ul>)
    bullets = []
  }
  for (const line of text.split('\n')) {
    const b = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/)
    if (b) { bullets.push(b[1]); continue }
    flush()
    if (line.trim()) blocks.push(<p key={`p${blocks.length}`}>{inline(line.trim(), sources)}</p>)
  }
  flush()
  return <div className="ask-text">{blocks}</div>
}

/** An Ask answer: text with citations, sources, and every named game with 📺 / 💻 play buttons. */
export function AskAnswer({ result }: { result: AskResult }) {
  const navigate = useNavigate()
  const toast = useToast()

  const onC64 = async (g: AskGame) => {
    try {
      if (g.gameId) await api.play(g.gameId)
      else if (g.catalog) await api.catalogPlay(g.catalog)
      toast(`📺 Loading ${g.title} on your C64…`, 'ok')
      navigate('/stream')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }
  const inBrowser = async (g: AskGame) => {
    try {
      const id = g.gameId ?? (g.catalog ? (await api.catalogFetch(g.catalog)).gameId : null)
      if (id) navigate(`/emulate/${id}`)
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  return (
    <div className="ask-answer">
      <Formatted text={result.answer} sources={result.sources} />
      {result.games.length > 0 && (
        <div className="ask-games">
          {result.games.map((g) => (
            <div key={g.title} className="ask-game">
              <span className="ask-game-title">
                {g.title}
                <span className="muted small">{g.gameId ? ' · in your library' : g.catalog ? ` · ${g.catalog.source}`
                  : g.elsewhere?.length ? ` · ${g.elsewhere.map((e) => e.label).join(', ')}` : ' · not found to play'}</span>
              </span>
              {(g.gameId || g.catalog)
                ? <PlayChoice onC64={() => onC64(g)} onBrowser={() => inBrowser(g)} browserOk={g.browserOk} />
                : g.elsewhere?.length ? <ElsewhereLinks title={g.title} results={g.elsewhere} /> : null}
            </div>
          ))}
        </div>
      )}
      {result.sources.length > 0 && (
        <div className="ask-sources small">
          Sources: {result.sources.map((s) => (
            <a key={s.n} href={s.url} target="_blank" rel="noreferrer" title={s.url}>[{s.n}] {s.title}</a>
          ))}
        </div>
      )}
      <div className="muted small">
        {result.webSearch ? '🔎 Web search (Brave) + ' : ''}🤖 {result.model || 'AI'}
        {result.searchNote ? ` · web search skipped: ${result.searchNote}` : ''}
        {!result.webSearch && !result.searchNote ? ' · from the model’s own knowledge (add a Brave key in Settings for web search)' : ''}
      </div>
    </div>
  )
}
