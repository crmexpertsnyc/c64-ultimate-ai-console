import { get, post, qs, request } from './api'

export type Compat = 'confirmed' | 'unverified' | 'unknown'

export interface BbsSourceRef { source: string; label: string; url: string; seenAt: string; listingUpdated: string | null }

export interface Bbs {
  id: number
  name: string
  description: string | null
  host: string
  port: number
  protocol: 'telnet' | 'raw'
  location: string | null
  website: string | null
  software: string | null
  petscii: Compat
  ansi: Compat
  compatNote: string | null
  sources: BbsSourceRef[]
  review: 'pending' | 'approved' | 'rejected'
  approved: boolean
  status: 'unknown' | 'reachable' | 'unreachable'
  lastCheckAt: string | null
  lastOkAt: string | null
  listed: boolean
  addedAt: string | null
  favorite: boolean
  notes: string
  lastConnectedAt: string | null
  thumbUrl: string | null      // from the board's own web page
  artPage: string | null
  // admin only
  lastError?: string | null
  failCount?: number
  nextCheckAt?: string | null
}

export interface BbsSourceInfo { name: string; label: string; url: string; terms: string; setting: string; enabled: boolean }

export interface BbsList {
  boards: Bbs[]
  counts: Record<'pending' | 'approved' | 'rejected', number>
  setupPending: boolean
  sources: BbsSourceInfo[]
  lastImport: { at: string; results: Record<string, { records?: number; added?: number; updated?: number; error?: string }> } | null
  lastCheckAt: string | null
  total: number
  dialOnC64: boolean
  autoApprove: boolean
  admin: boolean
}

export interface BbsQuery { q?: string; terminal?: 'petscii' | 'ansi'; favorites?: boolean; reachable?: boolean; review?: string }

export interface BbsHardware { dialOnC64: boolean; modem: Record<string, string | number | boolean> | null; error: string | null; terminalGameId: number | null; dialing: boolean }

export interface DialResult { ok: boolean; steps: string[]; error?: string }

export const bbsApi = {
  list: (query: BbsQuery = {}) => get<BbsList>(`/api/bbs${qs(query as Record<string, unknown>)}`),
  get: (id: number) => get<Bbs>(`/api/bbs/boards/${id}`),
  setMine: (id: number, body: { favorite?: boolean; notes?: string }) =>
    request<{ favorite: boolean; notes: string; lastConnectedAt: string | null }>('PUT', `/api/bbs/boards/${id}/me`, body),
  hardware: () => get<BbsHardware>('/api/bbs/hardware'),
  refresh: () => post<{ added: number; updated: number; results: Record<string, unknown>; errors?: string[] }>('/api/bbs/refresh'),
  review: (id: number, decision: 'approve' | 'reject' | 'pending') => post<Bbs>(`/api/bbs/boards/${id}/${decision}`),
  edit: (id: number, body: Partial<{ name: string; description: string; location: string; website: string; protocol: 'telnet' | 'raw'; petscii: Compat; ansi: Compat; compat_note: string }>) =>
    request<Bbs>('PATCH', `/api/bbs/boards/${id}`, body),
  add: (body: { name: string; host: string; port: number; protocol: 'telnet' | 'raw'; description?: string; website?: string; source_url?: string }) =>
    post<Bbs>('/api/bbs/boards', body),
  approveReachable: () => post<{ approved: number; stillPending: number }>('/api/bbs/approve-reachable'),
  findArt: () => post<{ checked: number; found: number }>('/api/bbs/art/refresh'),
  clearArt: (id: number) => request<Bbs>('DELETE', `/api/bbs/art/${id}`),
  installTerminal: () => post<{ gameId: number; added: boolean }>('/api/bbs/c64/install-terminal'),
  dialC64: (id: number) => post<DialResult>(`/api/bbs/boards/${id}/dial-c64`),
  hangUp: () => post<{ ok: boolean }>('/api/bbs/c64/hang-up'),
  check: (id: number) => post<Bbs>(`/api/bbs/boards/${id}/check`),
}

export function connectionText(b: Bbs): string {
  return `${b.name}\nHost: ${b.host}\nPort: ${b.port}\nTelnet: telnet ${b.host} ${b.port}\nC64 modem: ATDT ${b.host}:${b.port}`
}
