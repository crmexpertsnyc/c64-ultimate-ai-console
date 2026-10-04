import { useEffect, useState } from 'react'
import { Thumbs } from '../components/Thumbs'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, LaunchProgress, Spinner, formatBytes } from '../components/common'
import { CoverArt } from '../components/CoverArt'
import { useToast } from '../components/Toasts'
import { useLive } from '../hooks/useLive'
import { api, errorMessage } from '../services/api'
import type { Game } from '../shared/types'
import { CommandBar } from '../components/CommandBar'
import { ControlsCard } from '../components/ControlsCard'
import { GuideCard } from '../components/GuideCard'
import { AchievementsCard } from '../components/AchievementsCard'
import { MagazineReviewsCard } from '../components/MagazineReviewsCard'
import { GearSuggestions } from '../components/GearSuggestions'
import { PlayChoice, canPlayInBrowser } from '../components/PlayChoice'

const METHODS = ['auto', 'run_prg', 'load_prg', 'run_crt', 'mount_and_load', 'mount_only', 'dma_first_prg', 't64_extract', 'sid', 'mod']

export function GameDetail() {
  const { id } = useParams()
  const gameId = Number(id)
  const [game, setGame] = useState<Game | null>(null)
  const [disk, setDisk] = useState(1)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Partial<Game>>({})
  const [filling, setFilling] = useState(false)
  const [problems, setProblems] = useState<{ id: number; categoryLabel: string; occurrences: number }[]>([])
  useEffect(() => { api.issues('active', gameId).then((r) => setProblems(r.issues)).catch(() => {}) }, [gameId])
  const { job } = useLive()
  const toast = useToast()
  const navigate = useNavigate()

  useEffect(() => {
    api.game(gameId).then((g) => { setGame(g); setDisk(g.media?.[0]?.diskNumber ?? 1) })
      .catch((e) => toast(errorMessage(e), 'error'))
  }, [gameId, toast, job?.status])

  if (!game) return <div className="center"><Spinner /></div>

  const media = game.media ?? []
  const disks = media.filter((m) => ['d64', 'd71', 'd81', 'g64', 'g71'].includes(m.format))
  const current = media.find((m) => m.diskNumber === disk) ?? media[0]
  const directory: { name: string; type: string; blocks: number }[] = current?.info?.directory ?? []

  const run = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn()
      toast(label, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const fillDetails = async () => {
    setFilling(true)
    try {
      const r = await api.fillDetails(game.id)
      setGame(await api.game(game.id))
      toast(r.filled.length || r.tags.length ? `✨ Filled in ${[...r.filled.map((f) => f.replace('_', ' ')), r.tags.length ? 'style tags' : ''].filter(Boolean).join(', ')}` : '✨ Nothing new found', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setFilling(false)
    }
  }

  const save = async () => {
    try {
      const updated = await api.editGame(game.id, draft)
      setGame(updated)
      setEditing(false)
      setDraft({})
      toast('Saved', 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  const field = <K extends keyof Game>(key: K, label: string, type: 'text' | 'number' | 'checkbox' | 'textarea' = 'text') => {
    const value = (key in draft ? draft[key] : game[key]) as any
    const onChange = (v: any) => setDraft((d) => ({ ...d, [key]: v }))
    return (
      <label className={`field ${type === 'checkbox' ? 'field-check' : ''}`}>
        <span>{label}</span>
        {type === 'checkbox' ? <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
          : type === 'textarea' ? <textarea value={value ?? ''} onChange={(e) => onChange(e.target.value)} rows={3} />
            : <input type={type} value={value ?? ''} onChange={(e) => onChange(type === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value)} />}
      </label>
    )
  }

  const myJob = job && job.gameId === game.id ? job : null

  return (
    <div className="page">
      <button className="btn btn-ghost back" onClick={() => navigate(-1)}>← Back</button>
      <div className="detail">
        <div className="detail-cover"><CoverArt game={game} size="lg" /></div>
        <div className="detail-main">
          <h1>{game.title} {game.favorite && <span className="star">★</span>}</h1>
          <div className="muted">{[game.publisher, game.year, game.genre, game.category].filter(Boolean).join(' · ')}</div>
          {game.alternateNames.length > 0 && <div className="muted small">Also known as: {game.alternateNames.join(', ')}</div>}
          {game.details?.description && <p className="game-desc">{game.details.description}</p>}
          {problems.length > 0 && (
            <div className="emu-issue-note small">🐞 Problems in the browser: {problems.map((p) => `${p.categoryLabel}${p.occurrences > 1 ? ` (${p.occurrences}×)` : ''}`).join(', ')}
              {' · '}<button className="linklike" onClick={() => navigate(`/compatibility?game=${game.id}`)}>see the log</button></div>
          )}
          <div className="tags">
            {game.tags.map((t) => <span key={t} className="tag">{t}</span>)}
            {game.details?.tags.map((t) => (
              <button key={`d-${t}`} className="tag tag-style" onClick={() => navigate(`/library?q=${encodeURIComponent(t)}`)}
                title={`More games tagged “${t}”`}>{t}</button>
            ))}
            <button className="btn btn-ghost btn-sm" disabled={filling} onClick={fillDetails}
              title="Look up genre, players, year, publisher, a description and style tags (web + AI). Only empty fields are filled.">
              {filling ? '✨ Looking it up…' : game.details ? '✨ ↻' : '✨ Fill in details'}</button>
          </div>

          {disks.length > 1 && (
            <div className="disk-pills">
              {disks.map((m) => (
                <button key={m.id} className={`pill ${m.diskNumber === disk ? 'on' : ''}`} onClick={() => setDisk(m.diskNumber)}>
                  Disk {m.diskNumber}{m.label ? ` · ${m.label}` : ''}
                </button>
              ))}
            </div>
          )}

          <div className="row-actions big">
            <PlayChoice size="lg"
              onC64={() => run(`📺 Loading ${game.title} on your C64`, async () => { await api.play(game.id, disk); navigate('/stream') })}
              onBrowser={() => navigate(`/emulate/${game.id}`)} browserOk={canPlayInBrowser(game.format, game.category)} />
            {disks.length > 0 && (
              <button className="btn btn-lg" onClick={() => run(`Disk ${disk} mounted`, () => api.mountGame(game.id, disk))}>◉ Mount disk {disk}</button>
            )}
            <button className="btn btn-lg" onClick={() => run(game.favorite ? 'Unfavorited' : 'Favorited', async () => {
              const r = await api.favorite(game.id); setGame({ ...game, favorite: r.favorite })
            })}>{game.favorite ? '★ Favorite' : '☆ Favorite'}</button>
            <Thumbs title={game.title} gameId={game.id} />
            <button className="btn btn-lg btn-ghost" onClick={() => run('Cover art updated', async () => {
              const r = await api.findCover(game.id)
              if (!r.coverUrl) throw new Error('No box art found for this title — play it and a screenshot will be used')
              setGame({ ...game, coverUrl: r.coverUrl })
            })}>🖼 Find box art</button>
            <button className="btn btn-lg btn-ghost" onClick={() => { setEditing(!editing); setDraft({}) }}>✎ Edit</button>
          </div>
          <LaunchProgress job={myJob} />
          <CommandBar compact context={{ selectedGameId: game.id }} />
        </div>
      </div>

      {editing && (
        <Card title="Edit metadata" actions={<>
          <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={!Object.keys(draft).length}>Save</button>
        </>}>
          <div className="form-grid">
            {field('title', 'Title')}
            {field('publisher', 'Publisher')}
            {field('year', 'Year', 'number')}
            {field('genre', 'Genre')}
            {field('players', 'Players (e.g. 1-2)')}
            <label className="field"><span>Joystick port</span>
              <select value={String((draft.joystickPort !== undefined ? draft.joystickPort : game.joystickPort) ?? '')}
                onChange={(e) => setDraft((d) => ({ ...d, joystickPort: e.target.value ? Number(e.target.value) : null }))}>
                <option value="">Unknown</option><option value="1">1</option><option value="2">2</option>
              </select>
            </label>
            <label className="field"><span>Launch method</span>
              <select value={draft.preferredLaunch ?? game.preferredLaunch} onChange={(e) => setDraft((d) => ({ ...d, preferredLaunch: e.target.value }))}>
                {METHODS.map((m) => <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>)}
              </select>
            </label>
            {field('loadCommand', 'Load command (default LOAD"*",8,1)')}
            {field('startupDelay', 'Startup delay (s)', 'number')}
            {field('loadTimeout', 'Load timeout (s)', 'number')}
            {field('coverUrl', 'Cover image URL')}
            {field('runAfterLoad', 'Type RUN after load', 'checkbox')}
            {field('resetBeforeLoad', 'Reset before load', 'checkbox')}
            {field('needsFire', 'Press fire to start', 'checkbox')}
            <label className="field field-wide"><span>Tags (comma separated)</span>
              <input value={(draft.tags ?? game.tags).join(', ')}
                onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) }))} />
            </label>
            <label className="field field-wide"><span>Alternate names (comma separated)</span>
              <input value={(draft.alternateNames ?? game.alternateNames).join(', ')}
                onChange={(e) => setDraft((d) => ({ ...d, alternateNames: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) }))} />
            </label>
            <div className="field-wide">{field('notes', 'Notes', 'textarea')}</div>
          </div>
        </Card>
      )}

      <div className="grid-2">
        <GuideCard gameId={game.id} />
        <ControlsCard game={game} />
      </div>

      <AchievementsCard gameId={game.id} />
      <MagazineReviewsCard gameId={game.id} title={game.title} />
      <GearSuggestions context="game" gameId={game.id} title="🛒 Hardware for this game" />

      <div className="grid-2">
        <Card title="Launch settings">
          <dl className="kv">
            <dt>Method</dt><dd>{game.preferredLaunch.replace(/_/g, ' ')}</dd>
            <dt>Load command</dt><dd><code>{game.loadCommand || 'LOAD"*",8,1'}</code>{game.runAfterLoad ? ' then RUN' : ''}</dd>
            <dt>Joystick</dt><dd>{game.joystickPort ? `port ${game.joystickPort}` : 'unknown'}</dd>
            <dt>Players</dt><dd>{game.players || '—'}</dd>
            <dt>Startup delay</dt><dd>{game.startupDelay}s{game.needsFire ? ' · presses fire' : ''}</dd>
            <dt>Played</dt><dd>{game.playCount}× {game.lastPlayed ? `· last ${new Date(game.lastPlayed).toLocaleString()}` : ''}</dd>
          </dl>
          {game.notes && <p className="notes">{game.notes}</p>}
        </Card>
        <Card title={`Files (${media.length})`}>
          <ul className="file-list">
            {media.map((m) => (
              <li key={m.id} className={m.missing ? 'missing' : ''}>
                <span className="tag">{m.format.toUpperCase()}</span>
                {media.length > 1 && <span className="muted">disk {m.diskNumber}</span>}
                <span className="path" title={m.path}>{m.path}</span>
                <span className="muted small">{formatBytes(m.size)}{m.missing ? ' · missing' : ''}</span>
              </li>
            ))}
          </ul>
          {directory.length > 0 && (
            <>
              <h3>Directory of disk {current?.diskNumber}{current?.info?.diskName ? ` — “${current.info.diskName}”` : ''}</h3>
              <pre className="c64-dir">{directory.map((e) => `${String(e.blocks).padEnd(5)}"${e.name}"`.padEnd(24) + e.type).join('\n')}</pre>
            </>
          )}
          {current?.info?.title && current.format === 'sid' && (
            <dl className="kv"><dt>Title</dt><dd>{current.info.title}</dd><dt>Author</dt><dd>{current.info.author}</dd>
              <dt>Released</dt><dd>{current.info.released}</dd><dt>Songs</dt><dd>{current.info.songs}</dd></dl>
          )}
        </Card>
      </div>
    </div>
  )
}
