import { useEffect, useState } from 'react'
import { api, errorMessage } from '../services/api'

/** Browse directories on the machine running the backend (read-only listing). */
export function FolderPicker({ onPick }: { onPick: (path: string) => void }) {
  const [path, setPath] = useState('')
  const [dirs, setDirs] = useState<string[]>([])
  const [parent, setParent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manual, setManual] = useState('')

  useEffect(() => {
    api.dirs(path).then((r) => { setDirs(r.dirs); setParent(r.parent); setError(null) })
      .catch((e) => setError(errorMessage(e)))
  }, [path])

  return (
    <div className="picker">
      <div className="type-form">
        <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="/Games, D:\C64 or \\nas\share\c64" aria-label="Folder path" />
        <button className="btn btn-primary" disabled={!manual.trim()} onClick={() => onPick(manual.trim())}>Add</button>
      </div>
      <div className="picker-head">
        <span className="muted small">{path || 'Drives / root'}</span>
        {path && <button className="btn btn-ghost btn-sm" onClick={() => setPath(parent ?? '')}>↑ Up</button>}
        {path && <button className="btn btn-sm" onClick={() => onPick(path)}>Use this folder</button>}
      </div>
      {error && <div className="error-text small">{error}</div>}
      <ul className="picker-list">
        {dirs.map((d) => (
          <li key={d}><button className="link" onClick={() => setPath(d)}>📁 {d.split(/[\\/]/).filter(Boolean).pop() ?? d}</button></li>
        ))}
        {!dirs.length && !error && <li className="muted small">No sub-folders.</li>}
      </ul>
    </div>
  )
}
