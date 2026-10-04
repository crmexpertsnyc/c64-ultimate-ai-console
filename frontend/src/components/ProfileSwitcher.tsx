import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, errorMessage } from '../services/api'
import { currentProfile, setCurrentProfile } from '../services/profile'
import { useToast } from './Toasts'

export interface Profile { id: number; name: string; emoji: string; color: string; kids: boolean }

export async function loadProfiles(): Promise<{ profiles: Profile[]; emojis: string[] }> {
  return api.profiles()
}

/** Switch to a profile: everything (recommendations, ratings, saves, recap) follows, so reload the app. */
export function switchTo(p: Profile, to?: string) {
  setCurrentProfile(p.id)
  window.location.href = to ?? window.location.pathname + window.location.search
}

/** 👪 Top-bar "who's playing" switcher, with family profile management. */
export function ProfileSwitcher() {
  const [data, setData] = useState<{ profiles: Profile[]; emojis: string[] } | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { loadProfiles().then(setData).catch(() => {}) }, [open])
  const me = data?.profiles.find((p) => String(p.id) === currentProfile()) ?? data?.profiles[0]
  if (!me) return null
  return (
    <>
      <button className="btn btn-ghost btn-sm profile-chip" onClick={() => setOpen(true)} title="Who's playing? Each family member has their own picks, ratings and saves">
        <span className="profile-emoji" style={{ background: me.color }}>{me.emoji}</span> <span className="hide-sm">{me.name}</span>
      </button>
      {open && data && createPortal(<WhoIsPlaying data={data} onClose={() => setOpen(false)} onChanged={() => loadProfiles().then(setData)} />, document.body)}
    </>
  )
}

/** "Who's playing?" — pick a profile, add one, edit or remove. Used by the top bar and by TV mode. */
export function WhoIsPlaying({ data, onClose, onChanged, big = false, afterPick, focusId }: {
  data: { profiles: Profile[]; emojis: string[] }
  onClose?: () => void
  onChanged: () => void
  big?: boolean
  afterPick?: (p: Profile) => void
  focusId?: number             // TV mode: the profile the gamepad is on
}) {
  const toast = useToast()
  const [editing, setEditing] = useState<Partial<Profile> | null>(null)
  const pick = (p: Profile) => (afterPick ? (setCurrentProfile(p.id), afterPick(p)) : switchTo(p))
  const save = async () => {
    if (!editing) return
    try {
      if (editing.id) await api.updateProfile(editing.id, editing)
      else await api.createProfile(editing)
      setEditing(null)
      onChanged()
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }
  const remove = async (p: Profile) => {
    if (!window.confirm(`Remove ${p.name}? Their recommendations and ratings are forgotten (saved games stay on disk).`)) return
    try {
      await api.deleteProfile(p.id)
      if (String(p.id) === currentProfile()) setCurrentProfile(1)
      setEditing(null)
      onChanged()
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }
  const body = (
    <div className={`who ${big ? 'who-big' : ''}`}>
      <h2>Who's playing?</h2>
      <div className="who-grid">
        {data.profiles.map((p) => (
          <div key={p.id} className="who-item">
            <button className={`who-card ${(focusId !== undefined ? focusId === p.id : String(p.id) === currentProfile()) ? 'on' : ''}`} onClick={() => pick(p)}>
              <span className="who-avatar" style={{ background: p.color }}>{p.emoji}</span>
              <span>{p.name}</span>
              {p.kids && <span className="muted small">👶 kids</span>}
            </button>
            {!big && <button className="btn btn-ghost btn-sm" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`}>✎</button>}
          </div>
        ))}
        {!big && data.profiles.length < 12 && (
          <button className="who-card who-add" onClick={() => setEditing({ name: '', emoji: data.emojis[1] ?? '🙂', color: '#2ecc71', kids: false })}>
            <span className="who-avatar">＋</span><span>Add someone</span>
          </button>
        )}
      </div>
      {editing && (
        <div className="who-edit">
          <input value={editing.name ?? ''} maxLength={40} placeholder="Name" autoFocus onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') save() }} />
          <div className="who-emojis">
            {data.emojis.map((em) => (
              <button key={em} className={`who-emoji ${editing.emoji === em ? 'on' : ''}`} onClick={() => setEditing({ ...editing, emoji: em })}>{em}</button>
            ))}
          </div>
          <label className="toggle small"><input type="color" value={editing.color ?? '#7c70da'} onChange={(e) => setEditing({ ...editing, color: e.target.value })} /> Colour</label>
          <label className="toggle small"><input type="checkbox" checked={!!editing.kids} onChange={(e) => setEditing({ ...editing, kids: e.target.checked })} />
            Child — family-friendly recommendations only</label>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={save} disabled={!editing.name?.trim()}>{editing.id ? 'Save' : 'Add'}</button>
            <button className="btn btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
            {editing.id && editing.id !== 1 && <button className="btn btn-ghost" onClick={() => remove(editing as Profile)}>Remove</button>}
          </div>
        </div>
      )}
      {!big && <p className="muted small">Each person gets their own recommendations, 👍 / 👎, weekly recap and Browser Play saves. Playlists are shared.</p>}
    </div>
  )
  if (big) return body
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Who's playing?" onClick={(e) => e.stopPropagation()}>
        {body}
        <div className="modal-foot"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  )
}
