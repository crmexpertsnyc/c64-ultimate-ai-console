import { useEffect, useState } from 'react'
import { api, errorMessage } from '../services/api'
import { useToast } from './Toasts'

/** 👍 / 👎 for a game — teaches the recommendations. Click again to clear. */
export function Thumbs({ title, gameId, value, onChange, compact = false }: {
  title: string
  gameId?: number | null
  value?: -1 | 0 | 1          // known rating (skips the lookup)
  onChange?: (v: -1 | 0 | 1) => void
  compact?: boolean
}) {
  const toast = useToast()
  const [v, setV] = useState<-1 | 0 | 1>(value ?? 0)
  useEffect(() => {
    if (value !== undefined) { setV(value); return }
    api.rating(title).then((r) => setV(r.value)).catch(() => {})
  }, [title, value])

  const set = async (next: -1 | 1) => {
    const target = v === next ? 0 : next
    try {
      await api.rate(title, target, gameId)
      setV(target)
      onChange?.(target)
      if (target === 1) toast(`👍 Noted — more like ${title} coming up`, 'ok')
      if (target === -1) toast(`👎 Got it — fewer games like ${title}`, 'ok')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }
  const cls = compact ? 'btn btn-ghost btn-sm thumb' : 'btn thumb'
  return (
    <span className="thumbs" role="group" aria-label={`Rate ${title}`}>
      <button className={`${cls} ${v === 1 ? 'on' : ''}`} onClick={() => set(1)} aria-pressed={v === 1}
        title={v === 1 ? 'You like this (click to clear)' : 'I like this — recommend more like it'}>👍</button>
      <button className={`${cls} ${v === -1 ? 'on bad' : ''}`} onClick={() => set(-1)} aria-pressed={v === -1}
        title={v === -1 ? 'Not for you (click to clear)' : 'Not for me — show fewer like it'}>👎</button>
    </span>
  )
}
