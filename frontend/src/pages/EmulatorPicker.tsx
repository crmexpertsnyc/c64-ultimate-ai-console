import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Empty, Spinner } from '../components/common'
import { CoverArt } from '../components/CoverArt'
import { canPlayInBrowser } from '../components/PlayChoice'
import { api, errorMessage } from '../services/api'
import type { Game } from '../shared/types'

/** Emulator home: pick a library title to play in the browser on this device. */
export function EmulatorPicker() {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [items, setItems] = useState<Game[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const t = window.setTimeout(() => {
      api.library({ q, limit: 60, ...(q ? {} : { recent: true }) })
        .then((r) => {
          const games = r.items.filter((g) => canPlayInBrowser(g.format, g.category))
          // Nothing played yet: fall back to the whole library instead of an empty "recent" list.
          if (!q && !games.length) return api.library({ limit: 60 }).then((all) => setItems(all.items.filter((g) => canPlayInBrowser(g.format, g.category))))
          setItems(games)
        })
        .catch((e) => setError(errorMessage(e)))
    }, q ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [q])

  return (
    <div className="page">
      <div className="page-head">
        <h1>💻 Browser Play</h1>
        <span className="muted">An emulator on this device — your C64 isn't used, so it works anywhere. To play on the real
          machine use <strong>📺 On my C64</strong> in the Library.</span>
      </div>
      <Card>
        <input className="emu-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your library…"
          aria-label="Search your library" autoFocus />
        <p className="muted small">
          {q ? 'Library matches' : 'Recently played'} · not in your library yet? Find it in the{' '}
          <button className="link" onClick={() => navigate('/catalog')}>Catalog</button> and press 💻 In browser.
        </p>
        {error ? <p className="error-text">{error}</p>
          : !items ? <div className="center"><Spinner /></div>
            : !items.length ? <Empty>No playable titles found.</Empty>
              : (
                <div className="game-grid">
                  {items.map((g) => (
                    <button key={g.id} className="emu-pick" onClick={() => navigate(`/emulate/${g.id}`)} title={`Play ${g.title} here`}>
                      <CoverArt game={g} />
                      <span className="emu-pick-title">{g.title}</span>
                      <span className="muted small">{[g.year, g.format.toUpperCase(), g.numDisks > 1 ? `${g.numDisks} disks` : null].filter(Boolean).join(' · ')}</span>
                    </button>
                  ))}
                </div>
              )}
      </Card>
    </div>
  )
}
