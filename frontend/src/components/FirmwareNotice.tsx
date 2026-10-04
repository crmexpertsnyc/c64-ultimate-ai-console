import { useEffect, useState } from 'react'
import '../pages/events.css'
import { useLive } from '../hooks/useLive'
import { eventsApi } from '../services/eventsApi'
import type { FirmwareStatus } from '../services/eventsApi'

/** 🧩 "Firmware X is available — you have Y." Shown only when the maker's download page lists a newer version than
 *  the device reports. Notification and links only: updating is a manual step (no update button, on purpose). */
export function FirmwareNotice({ className = '' }: { className?: string }) {
  const [fw, setFw] = useState<FirmwareStatus | null>(null)
  const { news, status } = useLive()
  const installed = status?.info?.firmwareVersion
  useEffect(() => {
    let alive = true
    eventsApi.firmware().then((f) => { if (alive) setFw(f) }).catch(() => { /* optional notice */ })
    return () => { alive = false }
  }, [news?.at, installed])
  if (!fw?.newer || !fw.latest) return null
  return (
    <div className={`firmware-notice ${className}`} role="status">
      <span className="fw-icon" aria-hidden>🧩</span>
      <span>
        <strong>{fw.product} firmware {fw.latest} is available</strong>
        {fw.installed && <> — you have {fw.installed}.</>}{' '}
        {fw.changelogUrl && <><a href={fw.changelogUrl} target="_blank" rel="noopener noreferrer">Release notes ↗</a> · </>}
        <a href={fw.downloadPage} target="_blank" rel="noopener noreferrer">Download page ↗</a>
        <span className="muted small"> {fw.note || "Updating is a manual step — follow Commodore's instructions."}</span>
      </span>
    </div>
  )
}
