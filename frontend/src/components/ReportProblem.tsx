import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../services/api'
import { Modal } from './common'
import { useToast } from './Toasts'

export const PROBLEMS: [string, string][] = [
  ['wont_load', "Won't load"], ['no_start', 'Never starts'], ['hangs', 'Hangs / freezes'], ['crash', 'Crashes / resets'],
  ['graphics', 'Graphics glitch'], ['sound', 'Sound problem'], ['controls', "Controls don't work"], ['slow', 'Too slow'],
  ['other', 'Other'],
]

/**
 * ⚑ Report a problem with this game in Browser Play. Diagnostics (file, speed, screen, recent input, emulator
 * errors, browser) and a screenshot are captured now and stored in the 🐞 compatibility log.
 */
export function ReportProblem({ gameId, title, collect, onClose }: {
  gameId: number
  title: string
  collect: () => Promise<{ diagnostics: Record<string, unknown>; screenshot: string | null }>
  onClose: () => void
}) {
  const toast = useToast()
  const navigate = useNavigate()
  const [category, setCategory] = useState('hangs')
  const [note, setNote] = useState('')
  const [shot, setShot] = useState(true)
  const [captured, setCaptured] = useState<{ diagnostics: Record<string, unknown>; screenshot: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { collect().then(setCaptured).catch(() => setCaptured({ diagnostics: {}, screenshot: null })) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const send = async () => {
    setBusy(true)
    try {
      await api.reportIssue({ gameId, category, source: 'user', note, diagnostics: captured?.diagnostics ?? {},
        screenshot: shot ? captured?.screenshot ?? null : null })
      toast(`🐞 Reported: ${title} — in the compatibility log`, 'ok')
      onClose()
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open title={`⚑ Report a problem — ${title}`} onClose={onClose}
      footer={<>
        <button className="btn btn-ghost" onClick={() => { onClose(); navigate('/compatibility') }}>Open the log</button>
        <button className="btn btn-primary" onClick={send} disabled={busy || !captured}>{busy ? 'Saving…' : '🐞 Save report'}</button>
      </>}>
      <label className="field"><span>What happened?</span>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {PROBLEMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label className="field"><span>Details (optional)</span>
        <textarea rows={3} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.stopPropagation()}
          placeholder="e.g. black screen after pressing fire on the title; worked on the real C64" />
      </label>
      {captured?.screenshot && (
        <label className="toggle small"><input type="checkbox" checked={shot} onChange={(e) => setShot(e.target.checked)} /> Include this screenshot
          <img className="report-shot" src={captured.screenshot} alt="The C64 screen now" /></label>
      )}
      <p className="muted small">Saved with it: the file and its fingerprint, emulator speed, what the screen shows, your last keys,
        emulator errors and the browser — so it can be investigated later. It stays on this console.</p>
    </Modal>
  )
}
