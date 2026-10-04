import { get, post, qs, request } from './api'

/** 📚 Classic C64 magazines (scans on the Internet Archive): issues, search index, 🤖 ask, 🤖 game reviews. */

export interface IndexJob {
  running: boolean; queued: boolean; done: number; total: number; current: string | null
  indexed: number; skipped: number; errors: number; lastError: string | null; cancelled: boolean
  startedAt: string | null; finishedAt: string | null
}

export interface MagazineSeries {
  id: string; name: string; about: string; coverUrl: string | null
  issueCount: number; indexed: number; indexing: boolean
  job: IndexJob | null; error: string | null; fetchedAt: string | null
}

export interface MagazineOverview { series: MagazineSeries[]; indexedTotal: number; fts: boolean }

export interface MagazineIssue {
  key: string; identifier: string; file: string | null; series: string; title: string
  date: string | null; number: number | null; indexed: boolean; indexStatus: string | null
  readerUrl: string; detailsUrl: string; coverUrl: string
}

export interface IssueList { series: { id: string; name: string; about: string }; error: string | null; issues: MagazineIssue[] }

export interface SnippetPart { text: string; hit: boolean }

export interface SearchHit {
  issueKey: string; series: string; seriesName: string; issueTitle: string; date: string | null
  chunk: number; snippet: string; parts: SnippetPart[]; link: string; readerUrl: string
}

export interface SearchResult { q: string; hits: SearchHit[]; mode: 'all' | 'any' | null }

export interface Citation {
  n: number; issueTitle: string; seriesName: string; date: string | null; link: string; readerUrl: string; snippet: string
}

export interface MagazineAnswer { answer: string; citations: Citation[] }

export interface MagazineReview {
  n: number; magazine: string; issue: string; date: string | null; score: string; verdict: string
  link: string; readerUrl: string; snippet: string; coverUrl?: string | null; issueKey?: string
  scoreUncertain?: boolean   // the scan's OCR was garbled: the score is the AI's best reading
}

export interface GameMagazineReviews { gameId: number; title?: string; reviews: MagazineReview[] | null; madeAt: string | null }

export const magazinesApi = {
  overview: () => get<MagazineOverview>('/api/magazines'),
  issues: (series: string, refresh = false) =>
    get<IssueList>(`/api/magazines/${encodeURIComponent(series)}/issues${qs({ refresh })}`),
  startIndex: (series: string) => post<{ job: IndexJob }>(`/api/magazines/${encodeURIComponent(series)}/index`),
  cancelIndex: (series: string) => request<{ job: IndexJob | null }>('DELETE', `/api/magazines/${encodeURIComponent(series)}/index`),
  search: (q: string, series?: string) => get<SearchResult>(`/api/magazines/search${qs({ q, series })}`),
  ask: (question: string) => post<MagazineAnswer>('/api/magazines/ask', { question }),
  gameReviews: (gameId: number) => get<GameMagazineReviews>(`/api/games/${gameId}/magazine-reviews`),
  findGameReviews: (gameId: number) => post<GameMagazineReviews>(`/api/games/${gameId}/magazine-reviews`),
}

/** "1985-05" → "May 1985". */
export function issueDate(date: string | null): string {
  if (!date) return ''
  const [y, m] = date.split('-').map(Number)
  if (!y) return date
  if (!m) return String(y)
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}
