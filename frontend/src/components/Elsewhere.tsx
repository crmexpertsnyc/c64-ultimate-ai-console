import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import type { CatalogItem } from '../shared/types'
import { PlayChoice } from './PlayChoice'
import { ArchivePlay } from './ArchiveSearch'
import { useToast } from './Toasts'

export interface ElsewhereResult {
  kind: 'itch' | 'csdb' | 'lemon64' | 'archive' | 'c64com' | 'gtw'
  id?: string                  // archive / c64com / gtw: the archive's id (downloaded by the console)
  title?: string
  note?: string | null
  url: string
  label: string
  playable?: boolean           // itch.io: has a play-in-browser build (played on itch, new tab)
  csdbId?: string
  catalog?: CatalogItem | null // CSDb release mirrored by Assembly64: playable right away
}

/** Read a picked/dropped file and add it to the library. Returns the new game id. */
export async function importFile(file: File, title?: string): Promise<number> {
  const r = await api.importFile(file, title)
  return r.gameId
}

/**
 * Where a game lives outside the catalog, with what you can do there:
 *   itch.io → ▶ Play on itch.io ↗ / Get it on itch.io ↗ (new tab), then ⬆ Add the downloaded file
 *   CSDb    → ⬇ Add from CSDb (downloads the release), Lemon64 → page link
 * Once added, the game plays 📺 on the C64 or 💻 in the browser like any other.
 */
export function ElsewhereLinks({ title, results }: { title: string; results: ElsewhereResult[] }) {
  const navigate = useNavigate()
  const toast = useToast()
  const picker = useRef<HTMLInputElement>(null)
  const [gameId, setGameId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  const added = (id: number, how: string) => {
    setGameId(id)
    toast(`${title} added to your library ${how}`, 'ok')
  }
  const fromCsdb = async (r: ElsewhereResult) => {
    setBusy(true)
    try { added((await api.importUrl(r.url, title)).gameId, 'from CSDb') } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }
  const onFile = async (f: File | undefined) => {
    if (!f) return
    setBusy(true)
    try { added(await importFile(f, title), `from ${f.name}`) } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }

  if (gameId) {
    return (
      <PlayChoice onC64={() => api.play(gameId).then(() => navigate('/stream')).catch((e) => toast(errorMessage(e), 'error'))}
        onBrowser={() => navigate(`/emulate/${gameId}`)} />
    )
  }
  const itch = results.find((r) => r.kind === 'itch')
  const csdb = results.find((r) => r.kind === 'csdb')
  const lemon = results.find((r) => r.kind === 'lemon64')
  const archive = results.find((r) => ['archive', 'c64com', 'gtw'].includes(r.kind) && r.id)
  return (
    <div className="elsewhere">
      {archive && (
        <>
          <ArchivePlay source={archive.kind as 'archive' | 'c64com' | 'gtw'} id={archive.id!} title={title} />
          <a className="btn btn-ghost btn-sm" href={archive.url} target="_blank" rel="noopener noreferrer"
            title={archive.title ?? archive.label}>{archive.label} ↗</a>
        </>
      )}
      {itch && (
        <a className="btn btn-sm" href={itch.url} target="_blank" rel="noopener noreferrer"
          title={itch.playable ? 'Plays in your browser on itch.io (opens a new tab) — you can also support the developer there'
            : 'Buy, name your price or download it on itch.io (new tab), then add the file here with ⬆'}>
          {itch.playable ? '▶ Play on itch.io ↗' : '🛒 Get it on itch.io ↗'}</a>
      )}
      {csdb && !csdb.catalog && (
        <button className="btn btn-sm" disabled={busy} onClick={() => fromCsdb(csdb)} title="Download this release from CSDb into your library">
          ⬇ Add from CSDb</button>
      )}
      {csdb && <a className="btn btn-ghost btn-sm" href={csdb.url} target="_blank" rel="noopener noreferrer">CSDb ↗</a>}
      {lemon && <a className="btn btn-ghost btn-sm" href={lemon.url} target="_blank" rel="noopener noreferrer">Lemon64 ↗</a>}
      <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => picker.current?.click()}
        title="Downloaded the game (e.g. from itch.io)? Add the .prg / .d64 / .crt / .zip file to your library">⬆ Add downloaded file</button>
      <input ref={picker} type="file" hidden accept=".prg,.d64,.d71,.d81,.g64,.t64,.tap,.crt,.p00,.x64,.sid,.zip"
        onChange={(e) => onFile(e.target.files?.[0])} />
    </div>
  )
}
