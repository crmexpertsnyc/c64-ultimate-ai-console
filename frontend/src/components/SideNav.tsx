import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

interface Item { to: string; label: string; icon: string; hint?: string; needsInput?: boolean; also?: string[] }
interface Group { id: string; label: string; icon: string; items: Item[] }

const HOME: Item = { to: '/', label: 'Home', icon: '◉', hint: "What's playing, your C64 and quick play" }
const SETTINGS: Item = { to: '/settings', label: 'Settings', icon: '⚙' }
const GROUPS: Group[] = [
  { id: 'play', label: 'Play', icon: '🎮', items: [
    { to: '/library', label: 'Library', icon: '▤', hint: 'Your games', also: ['/games'] },
    { to: '/catalog', label: 'Find games', icon: '⌕', hint: 'The online catalog', also: ['/assembly64'] },
    { to: '/emulate', label: 'Browser Play', icon: '◧', hint: 'Play in the emulator, anywhere' },
    { to: '/playlists', label: 'Playlists', icon: '♫', hint: 'Game nights' },
    { to: '/compatibility', label: 'Compatibility', icon: '⚑', hint: "Games that didn't work in the browser" },
    { to: '/gallery', label: 'Gallery', icon: '▦', hint: 'Screenshots & recordings' },
  ] },
  { id: 'c64', label: 'My C64 Ultimate', icon: '📺', items: [
    { to: '/stream', label: 'C64 Screen', icon: '▣', hint: 'Live picture + AI assist' },
    { to: '/controller', label: 'Controller', icon: '✥', needsInput: true },
    { to: '/jukebox', label: 'Jukebox', icon: '🎵', hint: 'SID music on the real chip' },
    { to: '/menu', label: 'Ultimate Menu', icon: '☰', hint: "The C64 Ultimate's own menu" },
    { to: '/troubleshooting', label: 'Troubleshooting', icon: '🩺' },
  ] },
  { id: 'discover', label: 'Discover', icon: '🌐', items: [
    { to: '/news', label: "What's new", icon: '🆕', hint: 'News, releases, videos, new hardware' },
    { to: '/events', label: 'Events', icon: '📅' },
    { to: '/magazines', label: 'Magazines', icon: '📚' },
  ] },
  { id: 'hobby', label: 'Hobby', icon: '🧰', items: [
    { to: '/collection', label: 'Collection', icon: '📦' },
    { to: '/shop', label: 'Hardware', icon: '🛒' },
    { to: '/repair', label: 'Repair', icon: '🔧' },
  ] },
]

const KEY = 'c64.navCollapsed'
const matches = (item: Item, path: string) =>
  [item.to, ...(item.also ?? [])].some((p) => p !== '/' && (path === p || path.startsWith(p + '/')))

/**
 * The console's navigation, grouped by what you're doing: 🎮 Play · 📺 My C64 Ultimate · 🌐 Discover · 🧰 Hobby.
 * Desktop: a sidebar with collapsible groups (remembered; the current page's group stays open).
 * Phones: a bottom bar — Home · Play · C64 · Discover · More — each opening its group as a sheet.
 */
export function SideNav({ online, canInput, fresh }: { online: boolean; canInput: boolean; fresh: number }) {
  const { pathname } = useLocation()
  const groups = GROUPS.map((g) => ({ ...g, items: g.items.filter((i) => !i.needsInput || canInput) }))
  const current = groups.find((g) => g.items.some((i) => matches(i, pathname)))?.id ?? null
  const [collapsed, setCollapsed] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] }
  })
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(collapsed)) } catch { /* private mode */ } }, [collapsed])
  const [sheet, setSheet] = useState<string | null>(null)
  useEffect(() => setSheet(null), [pathname])

  const toggle = (id: string) => setCollapsed((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))
  const badge = (n: number) => n > 0 && <span className="nav-badge" aria-label={`${n} new`}>{n > 99 ? '99+' : n}</span>
  const link = (i: Item, groupId?: string) => (
    <NavLink key={i.to} to={i.to} end={i.to === '/'} title={groupId === 'c64' && !online ? `${i.hint ?? i.label} — your C64 is offline` : i.hint}
      className={({ isActive }) => `nav-link ${isActive || matches(i, pathname) ? 'active' : ''} ${groupId === 'c64' && !online ? 'nav-dim' : ''}`}>
      <span className="nav-icon" aria-hidden>{i.icon}</span>
      <span className="nav-label">{i.label}</span>
      {i.to === '/news' && badge(fresh)}
    </NavLink>
  )
  const heading = (g: Group, open: boolean) => (
    <button className="nav-head" onClick={() => toggle(g.id)} aria-expanded={open} title={open ? 'Fold this group' : 'Show this group'}>
      <span aria-hidden>{g.icon}</span>
      <span className="nav-head-label">{g.label}</span>
      {g.id === 'c64' && <span className={`nav-dot ${online ? 'on' : ''}`} title={online ? 'C64 online' : 'C64 offline'} />}
      {g.id === 'discover' && !open && badge(fresh)}
      <span className="nav-caret" aria-hidden>{open ? '▾' : '▸'}</span>
    </button>
  )

  // phones: four group tabs; Hobby and Settings share "More"
  const tabs = [
    { id: 'play', label: 'Play', icon: '🎮', groups: ['play'] },
    { id: 'c64', label: 'C64', icon: '📺', groups: ['c64'] },
    { id: 'discover', label: 'Discover', icon: '🌐', groups: ['discover'] },
    { id: 'more', label: 'More', icon: '☰', groups: ['hobby'] },
  ]
  const onSettings = pathname.startsWith('/settings')
  const sheetTab = tabs.find((t) => t.id === sheet)

  return (
    <>
      <nav className="nav-desktop" aria-label="Main">
        {link(HOME)}
        {groups.map((g) => {
          const open = g.id === current || !collapsed.includes(g.id)
          return (
            <div key={g.id} className={`nav-section ${open ? 'open' : ''}`}>
              {heading(g, open)}
              {open && <div className="nav-items">{g.items.map((i) => link(i, g.id))}</div>}
            </div>
          )
        })}
        <div className="nav-bottom">{link(SETTINGS)}</div>
      </nav>

      <nav className="nav-mobile" aria-label="Main">
        <NavLink to="/" end className={({ isActive }) => `nav-tab ${isActive ? 'active' : ''}`}>
          <span aria-hidden>◉</span><span>Home</span>
        </NavLink>
        {tabs.map((t) => {
          const active = t.groups.includes(current ?? '') || (t.id === 'more' && onSettings)
          return (
            <button key={t.id} className={`nav-tab ${active ? 'active' : ''} ${sheet === t.id ? 'open' : ''}`}
              onClick={() => setSheet(sheet === t.id ? null : t.id)} aria-expanded={sheet === t.id}>
              <span aria-hidden>{t.icon}</span><span>{t.label}</span>
              {t.id === 'c64' && <span className={`nav-dot ${online ? 'on' : ''}`} />}
              {t.id === 'discover' && badge(fresh)}
            </button>
          )
        })}
      </nav>
      {sheetTab && (
        <div className="nav-sheet-backdrop" onClick={() => setSheet(null)}>
          <div className="nav-sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={sheetTab.label}>
            {groups.filter((g) => sheetTab.groups.includes(g.id)).map((g) => (
              <div key={g.id}>
                <div className="nav-sheet-head">{g.icon} {g.label}{g.id === 'c64' && <span className="muted small"> · {online ? 'online' : 'offline'}</span>}</div>
                {g.items.map((i) => link(i, g.id))}
              </div>
            ))}
            {sheetTab.id === 'more' && link(SETTINGS)}
          </div>
        </div>
      )}
    </>
  )
}
