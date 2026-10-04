import { useEffect } from 'react'
import '../pages/magazines.css'

export interface ReaderTarget { title: string; readerUrl: string; link: string }

/** 📖 The Internet Archive's own book reader for one issue, embedded (the scan stays on archive.org). */
export function MagazineReader({ target, onClose }: { target: ReaderTarget | null; onClose: () => void }) {
  useEffect(() => {
    if (!target) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [target, onClose])
  if (!target) return null
  return (
    <div className="mag-reader-backdrop" onClick={onClose}>
      <div className="mag-reader" role="dialog" aria-modal aria-label={target.title} onClick={(e) => e.stopPropagation()}>
        <header className="mag-reader-head">
          <strong className="mag-reader-title">📖 {target.title}</strong>
          <a className="btn btn-ghost btn-sm" href={target.link} target="_blank" rel="noopener noreferrer">open on archive.org ↗</a>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <iframe className="mag-reader-frame" src={target.readerUrl} title={target.title} allowFullScreen
          referrerPolicy="no-referrer" />
      </div>
    </div>
  )
}
