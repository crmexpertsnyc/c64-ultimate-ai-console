import type { MenuScreen } from '../shared/types'

/** Renders the 40×25 Ultimate menu matrix with C64 colours and reverse video. */
export function MenuScreenView({ screen }: { screen: MenuScreen }) {
  const palette = screen.palette
  return (
    <div className="c64-screen" style={{ background: palette[screen.background] }} role="img"
      aria-label={`Ultimate menu: ${screen.text}`}>
      {screen.rows.map((row) => (
        <div key={row.index} className={`c64-row ${row.selected ? 'c64-row-selected' : ''}`}>
          {(row.cells ?? []).map(([ch, fg, bg, rev], i) => {
            const fore = palette[fg]
            const back = palette[bg]
            const style = rev ? { color: back, background: fore } : { color: fore, background: back }
            return <span key={i} className="c64-cell" style={style}>{ch === ' ' ? ' ' : ch}</span>
          })}
        </div>
      ))}
    </div>
  )
}
