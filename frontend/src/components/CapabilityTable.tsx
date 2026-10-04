import type { Capabilities } from '../shared/types'
import { CapBadge } from './common'

export function CapabilityTable({ caps, compact = false }: { caps: Capabilities; compact?: boolean }) {
  const entries = Object.entries(caps.details)
  return (
    <div className="cap-table-wrap">
      <table className="cap-table">
        <thead><tr><th>Capability</th><th>State</th>{!compact && <th>Evidence</th>}</tr></thead>
        <tbody>
          {entries.map(([key, d]) => (
            <tr key={key}>
              <td>{d.label}<div className="muted small mono">{key}</div></td>
              <td><CapBadge state={d.state} /></td>
              {!compact && <td className="muted small evidence">{d.evidence}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        <strong>Unverified</strong> routes are documented but were not probed because probing would change machine state
        (e.g. reset). They become Supported after the first successful use.
      </p>
    </div>
  )
}
