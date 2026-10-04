import { useCallback, useEffect, useState } from 'react'
import { errorMessage } from '../services/api'
import { issueDate, magazinesApi } from '../services/magazinesApi'
import type { GameMagazineReviews, MagazineReview } from '../services/magazinesApi'
import { Card, Spinner } from './common'
import { useToast } from './Toasts'
import { MagazineReader } from './MagazineReader'
import type { ReaderTarget } from './MagazineReader'
import '../pages/magazines.css'

/** The reviewing issue's cover with the score on it (a plain score badge when there's no cover). */
function ReviewCover({ review, onOpen }: { review: MagazineReview; onOpen: () => void }) {
  const [failed, setFailed] = useState(false)
  const score = review.score ? `${review.scoreUncertain ? '≈' : ''}${review.score}` : ''
  const tip = review.scoreUncertain ? 'Best reading of a blurry scan — tap to check the page' : undefined
  if (!review.coverUrl || failed) return <span className="mag-score" title={tip}>{score || '—'}</span>
  return (
    <button className="mag-review-cover" onClick={onOpen} title={`Read ${review.magazine} · ${review.issue}`}>
      <img src={review.coverUrl} alt={`${review.magazine} ${review.issue} cover`} loading="lazy" decoding="async" onError={() => setFailed(true)} />
      {score && <span className="mag-review-score" title={tip}>{score}</span>}
    </button>
  )
}

/** 📚 What the classic magazines scored this game — found by 🤖 in the magazines made searchable. */
export function MagazineReviewsCard({ gameId, title }: { gameId: number; title: string }) {
  const toast = useToast()
  const [data, setData] = useState<GameMagazineReviews | null>(null)
  const [busy, setBusy] = useState(false)
  const [reader, setReader] = useState<ReaderTarget | null>(null)
  useEffect(() => {
    let live = true
    magazinesApi.gameReviews(gameId).then((d) => { if (live) setData(d) }).catch(() => {})
    return () => { live = false }
  }, [gameId])
  const closeReader = useCallback(() => setReader(null), [])
  const find = async () => {
    setBusy(true)
    try {
      const d = await magazinesApi.findGameReviews(gameId)
      setData(d)
      const n = d.reviews?.length ?? 0
      toast(n ? `📚 ${n} magazine review${n === 1 ? '' : 's'} found` : `No reviews of ${title} in the searchable magazines`, n ? 'ok' : 'info')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  const reviews = data?.reviews ?? null
  return (
    <Card title="📚 Magazine reviews" actions={
      <button className="btn btn-ghost btn-sm" onClick={find} disabled={busy}
        title={`Search the magazines you've made searchable for reviews of ${title}`}>
        {busy ? <><Spinner /> Reading…</> : reviews ? '🤖 ↻' : '🤖 Find reviews'}
      </button>
    }>
      {reviews === null && (
        <p className="muted small">See what Zzap!64, Commodore Format and others scored {title}. Make a magazine searchable under 📚 Magazines, then press 🤖 Find reviews.</p>
      )}
      {reviews && reviews.length === 0 && (
        <p className="muted small">No reviews of {title} in the searchable magazines yet — make more of them searchable under 📚 Magazines.</p>
      )}
      {reviews && reviews.length > 0 && (
        <ul className="mag-reviews">
          {reviews.map((r) => (
            <li key={`${r.issue}:${r.n}`}>
              <ReviewCover review={r} onOpen={() => setReader({ title: `${r.magazine} · ${r.issue}`, readerUrl: r.readerUrl, link: r.link })} />
              <span className="mag-review-body">
                <strong>{r.magazine}</strong> <span className="muted small">{r.issue}{r.date ? ` · ${issueDate(r.date)}` : ''}</span>
                {r.verdict && <span className="mag-verdict small">“{r.verdict}”</span>}
                <span className="mag-links">
                  <button className="btn btn-ghost btn-sm" onClick={() => setReader({ title: `${r.magazine} · ${r.issue}`, readerUrl: r.readerUrl, link: r.link })}>📖 Read</button>
                  <a className="btn btn-ghost btn-sm" href={r.link} target="_blank" rel="noopener noreferrer">archive.org ↗</a>
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {reviews && reviews.length > 0 && <p className="muted small">🤖 Read from the OCR text of the scans — tap 📖 to check the page.</p>}
      <MagazineReader target={reader} onClose={closeReader} />
    </Card>
  )
}
