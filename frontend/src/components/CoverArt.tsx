import { useState } from 'react'
import type { Game } from '../shared/types'

// C64 palette colours for generated cover tiles.
const TILE_COLORS = ['#40318d', '#68372b', '#588d43', '#8b5429', '#70a4b2', '#6f3d86', '#9a6759', '#6c5eb5']

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

/**
 * Box-art shaped (3:4) cover. Portrait box scans fill the tile; landscape pictures (screenshots,
 * title screens) are shown whole on a blurred copy of themselves instead of being cropped.
 */
export function CoverArt({ game, size = 'md' }: { game: Game; size?: 'md' | 'lg' }) {
  const [failed, setFailed] = useState(false)
  const [landscape, setLandscape] = useState(false)

  if (game.coverUrl && !failed) {
    return (
      <div className={`cover cover-${size} ${landscape ? 'cover-landscape' : ''}`}>
        {landscape && <img className="cover-blur" src={game.coverUrl} alt="" aria-hidden />}
        <img
          className="cover-img"
          src={game.coverUrl}
          alt=""
          loading="lazy"
          onLoad={(e) => setLandscape(e.currentTarget.naturalWidth > e.currentTarget.naturalHeight * 1.05)}
          onError={() => setFailed(true)}
        />
      </div>
    )
  }
  const color = TILE_COLORS[hash(game.title) % TILE_COLORS.length]
  const glyph = game.category === 'music' ? '♪' : game.format === 'crt' ? '▣' : game.format === 'prg' ? '▶' : '◉'
  return (
    <div className={`cover cover-${size} cover-gen`} style={{ background: color }} aria-hidden>
      <div className="cover-stripes"><i /><i /><i /><i /></div>
      <div className="cover-glyph">{glyph}</div>
      <div className="cover-title">{game.title}</div>
      <div className="cover-format">{game.format.toUpperCase()}{game.numDisks > 1 ? ` ×${game.numDisks}` : ''}</div>
    </div>
  )
}

/** Box art for a game that may not be in the library (recommendations, playlists): libretro box art, then the
 *  title screen, then the CSDb release's screenshot — found by the console from the title. */
export function titleArt(title: string, catalog?: { id: string; category: number } | null): string {
  const q = new URLSearchParams({ name: title })
  if (catalog && catalog.category <= 10 && /^\d+$/.test(catalog.id)) q.set('csdb', catalog.id)
  return `/api/art/title?${q}`
}
