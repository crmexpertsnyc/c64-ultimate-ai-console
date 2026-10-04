import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { AuditEntry, DeviceStatus, LaunchJob, Session } from '../shared/types'

interface LiveState {
  status: DeviceStatus | null
  socket: 'connecting' | 'open' | 'closed'
  audit: AuditEntry[]
  job: LaunchJob | null
  session: Session | null
  scan: any
  vision: any
  live: any
  covers: any
  coverVersion: number
  news: { added: number; titles: string[]; at: number } | null
  sendReleaseAll: () => void
  refreshStatus: (s: DeviceStatus) => void
}

const LiveContext = createContext<LiveState | null>(null)

export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<DeviceStatus | null>(null)
  const [socket, setSocket] = useState<LiveState['socket']>('connecting')
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [job, setJob] = useState<LaunchJob | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [scan, setScan] = useState<any>(null)
  const [vision, setVision] = useState<any>(null)
  const [live, setLive] = useState<any>(null)
  const [news, setNews] = useState<LiveState['news']>(null)
  const [covers, setCovers] = useState<any>(null)
  const [coverVersion, setCoverVersion] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let stopped = false
    let retry = 0
    let timer: number | undefined

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws
      setSocket('connecting')
      ws.onopen = () => {
        retry = 0
        setSocket('open')
      }
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        switch (msg.type) {
          case 'status':
            setStatus(msg.data)
            if (msg.data?.session) setSession(msg.data.session)
            break
          case 'audit':
            setAudit((a) => [msg.data, ...a].slice(0, 60))
            break
          case 'audit_backlog':
            setAudit(msg.data)
            break
          case 'launch':
            setJob(msg.data)
            break
          case 'session':
            setSession(msg.data)
            break
          case 'scan':
            setScan(msg.data)
            break
          case 'vision':
            setVision(msg.data)
            break
          case 'live':
            setLive(msg.data)
            break
          case 'covers':
            setCovers(msg.data)
            break
          case 'news':
            setNews({ ...msg.data, at: Date.now() })
            break
          case 'cover':
            setCoverVersion((v) => v + 1)
            break
        }
      }
      ws.onclose = () => {
        setSocket('closed')
        if (!stopped) {
          retry = Math.min(retry + 1, 6)
          timer = window.setTimeout(connect, 500 * 2 ** retry)
        }
      }
    }
    connect()
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      wsRef.current?.close()
    }
  }, [])

  const sendReleaseAll = useCallback(() => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'release_all' }))
  }, [])

  const value = useMemo<LiveState>(() => ({
    status, socket, audit, job, session, scan, vision, live, covers, coverVersion, news, sendReleaseAll, refreshStatus: setStatus,
  }), [status, socket, audit, job, session, scan, vision, live, covers, coverVersion, news, sendReleaseAll])

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

export function useLive(): LiveState {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive must be used inside LiveProvider')
  return ctx
}
