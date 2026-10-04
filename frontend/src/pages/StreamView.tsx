import { useEffect, useRef, useState } from 'react'
import { VideoCanvas } from '../components/VideoCanvas'
import { PcmPlayer } from '../services/pcmPlayer'

/**
 * Chrome-free picture + sound for OBS (Browser Source → URL http://<console>:8064/stream-view,
 * 1280×720, "Control audio via OBS"). Options: ?audio=0 to mute, ?bg=transparent.
 */
export function StreamView() {
  const params = new URLSearchParams(location.search)
  const withAudio = params.get('audio') !== '0'
  const transparent = params.get('bg') === 'transparent'
  const player = useRef<PcmPlayer | null>(null)
  const [blocked, setBlocked] = useState(false)

  useEffect(() => {
    document.title = 'C64 stream view'
    document.body.classList.add('stream-view-body')
    if (transparent) document.body.classList.add('transparent-body')
    if (withAudio) {
      player.current = new PcmPlayer()
      player.current.start()
      // OBS allows autoplay; a normal browser tab may need one click to unmute.
      window.setTimeout(() => setBlocked(!!player.current?.blocked), 500)
    }
    return () => {
      player.current?.stop()
      document.body.classList.remove('stream-view-body', 'transparent-body')
    }
  }, [withAudio, transparent])

  return (
    <div className="stream-view" onClick={async () => { if (blocked) setBlocked(!(await player.current?.resume())) }}>
      <VideoCanvas />
      {blocked && <div className="stream-view-unmute">Click to enable sound</div>}
    </div>
  )
}
