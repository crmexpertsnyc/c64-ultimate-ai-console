import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Game } from '../shared/types'
import { Card } from './common'

/**
 * "How do I control this game?" — for the real C64 Ultimate (joystick port, wireless keyboard) and for
 * 📱 Play here (the browser emulator). Keyboard mappings for the real machine are the ones reported by
 * C64 Ultimate users for USB keyboards; they are marked as such until confirmed on your machine.
 */
export function ControlsCard({ game, compact = false }: { game: Game; compact?: boolean }) {
  const port = game.joystickPort ?? 2
  return (
    <Card title="🎮 Controls" className="controls-card">
      <div className={compact ? 'controls-grid compact' : 'controls-grid'}>
        <section>
          <h3>📺 On my C64</h3>
          <dl className="kv">
            <dt>Joystick</dt>
            <dd>
              <strong>Port {port}</strong>
              {game.joystickPort ? '' : ' (most games; switch to port 1 if nothing moves)'}
              {game.players && game.players !== '1' && <> · {game.players} players — player 2 in port {port === 2 ? 1 : 2}</>}
            </dd>
            <dt>Wireless pad</dt><dd>Receiver (Unijoysticle, CX40+…) in port {port}</dd>
            {game.needsFire && <><dt>To start</dt><dd>Press <strong>fire</strong></dd></>}
          </dl>
          <p className="muted small">USB keyboard (e.g. K780 with its receiver in the C64U) — reported by C64U users:</p>
          <table className="keys-table">
            <tbody>
              <tr><td>RUN/STOP</td><td>Esc</td></tr>
              <tr><td>C= (Commodore)</td><td>Right Ctrl</td></tr>
              <tr><td>Cursor keys, letters, F1–F8</td><td>Same keys</td></tr>
              <tr><td>Ultimate menu</td><td>Scroll Lock, or the console's Menu page</td></tr>
            </tbody>
          </table>
        </section>
        <section>
          <h3>💻 In browser (emulator)</h3>
          <table className="keys-table">
            <tbody>
              <tr><td>Joystick</td><td>Arrow keys · gamepad D-pad · touch</td></tr>
              <tr><td>Fire</td><td>Space · gamepad A, B or Y</td></tr>
              <tr><td>Y/N, letters, Enter</td><td>Type normally (also while playing)</td></tr>
              <tr><td>RUN/STOP</td><td>Ctrl+R · on-screen button</td></tr>
              <tr><td>C= (Commodore)</td><td>Ctrl</td></tr>
              <tr><td>RESTORE</td><td>Page Up</td></tr>
              <tr><td>RETURN</td><td>Enter · gamepad Start</td></tr>
              <tr><td>F1 – F8</td><td>F1–F8 buttons above the game · same keys (F2/F4/F6/F8 = Shift+F1/F3/F5/F7) · gamepad shoulders/triggers</td></tr>
              <tr><td>Space bar on start screens</td><td>Space (Auto knows when a screen wants it)</td></tr>
              <tr><td>Cursor keys</td><td>Switch to ⌨ Type (Ctrl+Alt+G)</td></tr>
              <tr><td>Swap joystick port</td><td>Ctrl+Alt+P</td></tr>
              <tr><td>Leave the game</td><td>Shift+Esc (plain Esc stays with the browser)</td></tr>
            </tbody>
          </table>
        </section>
      </div>
      {game.notes && <p className="notes"><strong>Notes:</strong> {game.notes}</p>}
    </Card>
  )
}

/** Controls for the game that is running now (Display page). */
export function CurrentControls({ gameId }: { gameId: number | null | undefined }) {
  const [game, setGame] = useState<Game | null>(null)
  useEffect(() => {
    if (!gameId) { setGame(null); return }
    api.game(gameId).then(setGame).catch(() => setGame(null))
  }, [gameId])
  return game ? <ControlsCard game={game} compact /> : null
}
