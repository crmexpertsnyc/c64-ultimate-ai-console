import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { hobbyApi } from '../services/hobbyApi'
import type { Suggestion } from '../services/hobbyApi'
import { SuggestionList } from '../pages/ShopPage'
import { Card } from './common'
import '../pages/hobby.css'

/**
 * 🛒 Hardware that fits what the console knows: a game that needs an REU / EasyFlash / mouse, playing with the
 * keyboard as the joystick, parts for a repair. Renders nothing when there's nothing useful to suggest.
 */
export function GearSuggestions({ context, gameId, using, title = '🛒 Gear for this' }: {
  context: 'game' | 'controller' | 'setup'
  gameId?: number
  using?: string
  title?: string
}) {
  const [s, setS] = useState<Suggestion[]>([])
  useEffect(() => {
    hobbyApi.suggest({ context, game_id: gameId, using }).then((r) => setS(r.suggestions)).catch(() => setS([]))
  }, [context, gameId, using])
  if (!s.length) return null
  return (
    <Card title={title} className="gear-card" actions={<Link className="btn btn-ghost btn-sm" to="/shop">All hardware →</Link>}>
      <SuggestionList suggestions={s} />
      <p className="muted small">Links go to the sellers' own shops.</p>
    </Card>
  )
}
