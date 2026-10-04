import type { InputProfileInfo } from './startAnalyzer'
import { currentProfile } from './profile'
import type {
  AuditEntry, CatalogItem, Capabilities, CoverState, LiveStatus, Recording, Screenshot, CommandResult, DeviceStatus, Game, JoyBridgeStatus, LaunchJob, MenuScreen, Session, Settings,
} from '../shared/types'

export class ApiError extends Error {
  status: number
  kind?: string
  constructor(status: number, message: string, kind?: string) {
    super(message)
    this.status = status
    this.kind = kind
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-C64-Source': 'ui', 'X-C64-Profile': currentProfile() },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (res.status === 401 && data?.kind === 'auth') window.dispatchEvent(new Event('c64-auth-required'))
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? data)
    throw new ApiError(res.status, detail || res.statusText, data?.kind)
  }
  return data as T
}

export const get = <T,>(p: string) => request<T>('GET', p)
export const post = <T,>(p: string, b?: unknown) => request<T>('POST', p, b ?? {})

export interface DetailsJob { running: boolean; done: number; total: number; filled: number; errors: number; current: string | null; lastError?: string | null }

export interface LibraryQuery {
  q?: string
  favorites?: boolean
  recent?: boolean
  format?: string
  publisher?: string
  year?: number
  genre?: string
  category?: string
  multiplayer?: boolean
  joystick_port?: number
  limit?: number
  offset?: number
}

export function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '' && v !== false) u.set(k, String(v))
  })
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const api = {
  health: () => get<{ ok: boolean; version: string; connected: boolean; simulated: boolean; setupComplete: boolean; frontendBuild: string | null }>('/api/health'),
  device: () => get<DeviceStatus>('/api/device'),
  capabilities: () => get<Capabilities>('/api/capabilities'),
  reconnect: () => post<DeviceStatus>('/api/device/connect'),
  reset: () => post('/api/device/reset'),
  powerState: () => get<{ c64: 'on' | 'off'; canPowerOff: boolean; plug: { configured: boolean; type: string | null; host: string | null; on: boolean | null } }>('/api/device/power'),
  powerOn: () => post<{ ok: boolean; via: string; note: string }>('/api/device/power-on'),
  reboot: () => post('/api/device/reboot'),
  pause: () => post('/api/device/pause'),
  resume: () => post('/api/device/resume'),
  powerOff: () => post('/api/device/power-off', { confirm: true }),
  menuRead: () => get<{ open: boolean; screen: MenuScreen | null }>('/api/device/menu'),
  menu: (action: string) => post<any>('/api/device/menu', { action }),
  key: (key: string, transition: 'tap' | 'press' | 'release' = 'tap') => post('/api/device/input', { key, transition }),
  type: (text: string, press_return = false) => post<{ typed: number; mode: string }>('/api/device/type', { text, press_return }),
  joystick: (inputs: string[], transition: 'tap' | 'press' | 'release', port?: number) =>
    post('/api/device/joystick', { inputs, transition, port }),
  joystickPort: (port: number) => post('/api/device/joystick/port', { port }),
  joybridge: () => get<JoyBridgeStatus>('/api/joybridge'),
  joybridgeDiscover: () => post<{ found: (Partial<JoyBridgeStatus> & { host: string })[] }>('/api/joybridge/discover'),
  joybridgeTest: (port?: number) => post<{ ok: boolean; port: number; sent: string[] }>('/api/joybridge/test', { port }),
  releaseAll: () => post<{ sent: boolean }>('/api/device/release-all'),
  drives: () => get<any[]>('/api/device/drives'),
  driveAction: (drive: string, action: 'remove' | 'reset' | 'on' | 'off') => post(`/api/device/drives/${drive}/${action}`),
  driveMode: (drive: string, mode: string) => post(`/api/device/drives/${drive}/mode`, { mode }),
  mount: (drive: string, path: string, storage: 'device' | 'local') => post(`/api/device/drives/${drive}/mount`, { path, storage }),
  runOnDevice: (kind: string, path: string) => post('/api/device/run', { kind, path }),
  screen: () => get<{ available: boolean; lines: string[] | null }>('/api/device/screen'),

  library: (q: LibraryQuery) => get<{ total: number; items: Game[] }>(`/api/library${qs(q as Record<string, unknown>)}`),
  facets: () => get<{ formats: string[]; publishers: string[]; years: number[]; genres: string[]; categories: string[] }>('/api/library/facets'),
  stats: () => get<{ games: number; media: number; favorites: number; roots: { path: string; lastScan: string | null; fileCount: number; lastError: string | null }[] }>('/api/library/stats'),
  scan: (paths: string[]) => post<any>('/api/library/scan', { paths }),
  scanStatus: () => get<any>('/api/library/scan'),
  dirs: (path: string) => get<{ path: string; parent: string | null; dirs: string[] }>(`/api/fs/dirs${qs({ path })}`),
  game: (id: number) => get<Game>(`/api/games/${id}`),
  editGame: (id: number, changes: Partial<Game>) => request<Game>('PATCH', `/api/games/${id}`, changes),
  favorite: (id: number) => post<{ favorite: boolean }>(`/api/games/${id}/favorite`),
  play: (id: number, disk?: number, method?: string) => post<LaunchJob>(`/api/games/${id}/play`, { disk, method }),
  mountGame: (id: number, disk: number) => post<{ session: Session }>(`/api/games/${id}/mount`, { disk }),

  currentSession: () => get<{ session: Session; job: LaunchJob | null }>('/api/current-session'),
  sessionDisk: (n: number) => post<Session>(`/api/session/disk/${n}`),
  nextDisk: () => post<Session>('/api/session/next-disk'),
  previousDisk: () => post<Session>('/api/session/previous-disk'),

  command: (text: string, context: Record<string, unknown> = {}, confirm = false) =>
    post<CommandResult>('/api/command', { text, context, confirm }),
  examples: () => get<{ intent: string; example: string }[]>('/api/command/examples'),

  settings: () => get<Settings>('/api/settings'),
  saveSettings: (s: Settings) => request<Settings>('PUT', '/api/settings', s),
  testConnection: (host: string, port: number, password: string, protocol = 'http') =>
    post<any>('/api/setup/test-connection', { host, port, password, protocol }),
  ai: () => get<{ provider: string; model: string; baseUrl: string; configured: boolean; local: boolean }>('/api/ai'),
  aiTest: (unsaved: Record<string, unknown> = {}) => post<any>('/api/ai/test', unsaved),

  audit: (params: { limit?: number; failures?: boolean; source?: string } = {}) =>
    get<AuditEntry[]>(`/api/audit${qs(params)}`),
  troubleshooting: () => get<any>('/api/troubleshooting'),

  streams: () => get<any>('/api/streams'),
  stream: (kind: 'video' | 'audio', action: 'start' | 'stop') => post<any>(`/api/streams/${kind}/${action}`),

  serverJson: () => get<any>('/api/assembly64/server-json'),
  generateServer: (body: { name: string; host: string; port: number; client_id: string }) =>
    post<any>('/api/assembly64/generate', body),
  homeSearch: (server: string, query: string) => post<any[]>('/api/assembly64/search', { server, query }),
  homeEntries: (server: string, entry_id: string, category: string) =>
    get<any[]>(`/api/assembly64/entries${qs({ server, entry_id, category })}`),
  homeImport: (body: { server: string; entry_id: string; category: string; content_id: string; filename: string }) =>
    post<any>('/api/assembly64/import', body),

  catalog: () => get<{ configured: boolean; url: string; clientId: string; cacheDir: string }>('/api/catalog'),
  catalogSearch: (q: string, kind = 'games') => get<CatalogItem[]>(`/api/catalog/search${qs({ q, kind })}`),
  catalogEntries: (category: number, id: string) =>
    get<{ id: number; path: string; size: number; selected: boolean }[]>(`/api/catalog/entries/${category}/${encodeURIComponent(id)}`),
  emulatorFiles: (id: number) => get<{ id: number; title: string; files: { mediaId: number; name: string; format: string; disk: number; url: string }[] }>(`/api/games/${id}/emulator`),
  saveInfo: (id: number) => get<SaveInfo>(`/api/games/${id}/save/info`),
  deleteSave: (id: number) => request<{ ok: boolean }>('DELETE', `/api/games/${id}/save`),
  authStatus: () => get<AuthStatus>('/api/auth/status'),
  login: (password: string) => post<{ ok: boolean }>('/api/auth/login', { password }),
  logout: () => post<{ ok: boolean }>('/api/auth/logout'),
  setPassword: (current: string, next: string) => request<{ ok: boolean; enabled: boolean }>('PUT', '/api/auth/password', { current, new: next }),
  handoffToBrowser: () => post<{ reset: boolean; title?: string }>('/api/device/handoff-to-browser'),
  ask: (question: string) => post<import('../components/AskAnswer').AskResult>('/api/ask', { question }),
  testSearch: (key?: string) => post<{ ok: boolean; results?: number; sample?: string; error?: string }>('/api/ask/test-search', { BRAVE_API_KEY: key || '' }),
  importFile: async (file: File, title?: string) => {
    const qs = new URLSearchParams({ filename: file.name, ...(title ? { title } : {}) })
    const res = await fetch(`/api/library/import?${qs}`, { method: 'POST', body: file, headers: { 'X-C64-Source': 'ui', 'X-C64-Profile': currentProfile() } })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new ApiError(res.status, data?.detail || res.statusText)
    return data as { gameId: number }
  },
  searchSources: (q: string) => get<import('../components/ArchiveSearch').ArchiveSearchResult>(`/api/sources/search?q=${encodeURIComponent(q)}`),
  importSource: (source: string, id: string, title?: string) => post<{ gameId: number }>('/api/library/import-source', { source, id, title }),
  importUrl: (url: string, title?: string) => post<{ gameId: number }>('/api/library/import-url', { url, title }),
  findSources: (title: string) => get<{ title: string; results: import('../components/Elsewhere').ElsewhereResult[] }>(`/api/sources/find?title=${encodeURIComponent(title)}`),
  guide: (id: number) => get<{ guide: import('../components/GuideCard').Guide | null }>(`/api/games/${id}/guide`),
  makeGuide: (id: number) => post<{ guide: import('../components/GuideCard').Guide }>(`/api/games/${id}/guide`),
  putSmartStart: (id: number, body: { steps: SmartStep[]; auto: boolean; source: 'recorded' | 'guide' }) => request<{ ok: boolean }>('PUT', `/api/games/${id}/smart-start`, body),
  deleteSmartStart: (id: number) => request<{ ok: boolean }>('DELETE', `/api/games/${id}/smart-start`),
  inputProfile: (id: number) => request<InputProfileInfo>('GET', `/api/games/${id}/input-profile`),
  learnInputProfile: (id: number, body: { sha256: string; startupSequence?: { key: string; when?: string }[]; joystickPort?: number }) =>
    request<InputProfileInfo>('PUT', `/api/games/${id}/input-profile/learned`, body),
  forgetInputProfile: (id: number) => request<{ ok: boolean }>('DELETE', `/api/games/${id}/input-profile/learned`),
  fillDetails: (id: number) => post<{ gameId: number; filled: string[]; description: string | null; tags: string[]; genre: string | null }>(`/api/games/${id}/details`, {}),
  fillAllDetails: (onlyMissing = true) => post<DetailsJob>('/api/library/details', { onlyMissing }),
  detailsJob: () => get<DetailsJob>('/api/library/details'),
  hint: (id: number, body: { screen: string; question?: string; level: number; previous?: string[] }) =>
    post<{ hint: string; level: number; confidence: string; sources: { n: number; title: string; url: string }[]; webSearch: boolean }>(`/api/games/${id}/hint`, body),
  copilot: (id: number, transcript: string, notes: { map?: Record<string, string[]>; inventory?: string[] }) =>
    post<import('../components/Copilot').CopilotReply>(`/api/games/${id}/copilot`, { transcript, notes }),
  alternatives: (id: number) => get<import('../components/HangRescue').Alternatives>(`/api/games/${id}/alternatives`),
  reportHang: (id: number) => post<{ hangs: number }>(`/api/games/${id}/hang`, {}),
  playlists: () => get<{ playlists: import('../pages/PlaylistsPage').PlaylistSummary[]; suggestions: string[] }>('/api/playlists'),
  makePlaylist: (prompt: string) => post<import('../pages/PlaylistsPage').Playlist>('/api/playlists', { prompt }),
  playlist: (id: number) => get<import('../pages/PlaylistsPage').Playlist>(`/api/playlists/${id}`),
  playlistItem: (id: number, index: number, body: { done?: boolean; remove?: boolean }) =>
    request<import('../pages/PlaylistsPage').Playlist>('PATCH', `/api/playlists/${id}/items/${index}`, body),
  deletePlaylist: (id: number) => request<{ ok: boolean }>('DELETE', `/api/playlists/${id}`),
  news: (p: { kind?: string; category?: string; q?: string; limit?: number; offset?: number; sort?: string }) =>
    get<import('../pages/NewsPage').NewsList>(`/api/news${qs(p)}`),
  newsCount: (since: string) => get<Record<string, number> & { total: number }>(`/api/news/new${qs({ since })}`),
  refreshNews: () => post<{ added: number; errors: Record<string, string>; checkedAt: string }>('/api/news/refresh', {}),
  newsDigest: () => get<{ digest: import('../pages/NewsPage').Digest | null }>('/api/news/digest'),
  makeNewsDigest: () => post<{ digest: import('../pages/NewsPage').Digest }>('/api/news/digest', {}),
  addNewsRelease: (id: number) => post<{ gameId: number }>(`/api/news/${id}/add`, {}),
  recap: () => get<import('../components/YourWeek').Recap>('/api/recap'),
  refreshRecap: () => post<import('../components/YourWeek').Recap>('/api/recap/refresh', {}),
  recapChallenge: (index: number, done: boolean) => post<import('../components/YourWeek').Recap>(`/api/recap/challenges/${index}`, { done }),
  reportIssue: (body: { gameId?: number | null; title?: string; category: string; source?: 'auto' | 'user'; note?: string; diagnostics?: Record<string, unknown>; screenshot?: string | null }) =>
    post<import('../pages/CompatPage').Issue>('/api/issues', body),
  issues: (status?: string, gameId?: number) => get<{ issues: import('../pages/CompatPage').Issue[]; categories: Record<string, string> }>(
    `/api/issues?${new URLSearchParams({ ...(status ? { status } : {}), ...(gameId ? { gameId: String(gameId) } : {}) })}`),
  updateIssue: (id: number, body: { status?: string; resolution?: string; note?: string }) => request<import('../pages/CompatPage').Issue>('PATCH', `/api/issues/${id}`, body),
  deleteIssue: (id: number) => request<{ ok: boolean }>('DELETE', `/api/issues/${id}`),
  investigateIssue: (id: number) => post<import('../pages/CompatPage').Issue>(`/api/issues/${id}/investigate`, {}),
  gameWorked: (id: number) => post<{ updated: number }>(`/api/games/${id}/worked`, {}),
  profiles: () => get<{ profiles: import('../components/ProfileSwitcher').Profile[]; current: number; emojis: string[] }>('/api/profiles'),
  createProfile: (p: Partial<import('../components/ProfileSwitcher').Profile>) => post<import('../components/ProfileSwitcher').Profile>('/api/profiles', p),
  updateProfile: (id: number, p: Partial<import('../components/ProfileSwitcher').Profile>) => request<import('../components/ProfileSwitcher').Profile>('PATCH', `/api/profiles/${id}`, p),
  deleteProfile: (id: number) => request<{ ok: boolean }>('DELETE', `/api/profiles/${id}`),
  saves: () => get<{ saves: { gameId: number; title: string; coverUrl: string | null; savedAt: number | null; device: string | null; thumb: string | null }[] }>('/api/saves'),
  netplayRoom: (gameId: number) => post<{ code: string; gameId: number; title: string; stun: string[] }>('/api/netplay/rooms', { gameId }),
  netplayInfo: (code: string) => get<{ code: string; title: string; hostOnline: boolean; guests: number; full: boolean; stun: string[] }>(`/api/netplay/rooms/${encodeURIComponent(code)}`),
  deviceProgress: () => post<{ gameId: number | null; metrics: Record<string, number>; newBest: number | null; unlocked: { title: string; icon: string; description: string }[]; sample: import('./startAnalyzer').ScreenSample }>('/api/device/progress', {}),
  gameProgress: (id: number, sample: unknown, playing: boolean) => post<{ metrics: Record<string, number>; read: Record<string, number>; newBest: number | null; unlocked: { title: string; icon: string; description: string }[] }>(`/api/games/${id}/progress`, { sample, playing }),
  gameAchievements: (id: number) => get<import('../components/AchievementsCard').GameAchievements>(`/api/games/${id}/achievements`),
  makeAchievements: (id: number) => post<{ achievements: import('../components/AchievementsCard').Achievement[] }>(`/api/games/${id}/achievements/generate`, {}),
  recentAchievements: () => get<{ recent: (import('../components/AchievementsCard').Achievement & { game: string; gameId: number; at: string; name: string; emoji: string; source: string })[] }>('/api/achievements/recent'),
  recommendations: () => get<import('../components/ForYou').Recommendations>('/api/recommendations'),
  refreshRecommendations: () => post<import('../components/ForYou').Recommendations>('/api/recommendations/refresh', {}),
  rate: (title: string, value: -1 | 0 | 1, gameId?: number | null) => post<{ title: string; value: number }>('/api/taste/rate', { title, value, gameId: gameId ?? null }),
  rating: (title: string) => get<{ title: string; value: -1 | 0 | 1 }>(`/api/taste/rating?title=${encodeURIComponent(title)}`),
  tasteEvent: (kind: 'play_browser' | 'session', gameId: number, minutes = 0) => post<{ ok: boolean }>('/api/taste/event', { kind, gameId, minutes }),
  tasteProfile: () => get<import('../components/ForYou').TasteProfile>('/api/taste/profile'),
  forgetGame: (title: string) => request<{ ok: boolean }>('DELETE', `/api/taste/game?title=${encodeURIComponent(title)}`),
  clearTaste: () => request<{ ok: boolean }>('DELETE', '/api/taste'),
  catalogFetch: (item: CatalogItem) => post<{ gameId: number }>('/api/catalog/fetch', item),
  catalogPlay: (item: CatalogItem) => post<{ gameId: number; job: LaunchJob }>('/api/catalog/play', item),

screenshot: (title?: string, game_id?: number) => post<Screenshot>('/api/screenshots', { title, game_id }),
  screenshots: () => get<Screenshot[]>('/api/screenshots'),
  deleteScreenshot: (name: string) => request<{ ok: boolean }>('DELETE', '/api/screenshots/' + encodeURIComponent(name)),
  setCover: (gameId: number, screenshot: string) => post<{ coverUrl: string }>('/api/games/' + gameId + '/cover', { screenshot }),
  refreshCovers: (force = false) => post<CoverState>('/api/library/covers', { force }),
  coverState: () => get<CoverState>('/api/library/covers'),
  findCover: (id: number) => post<{ source: string | null; coverUrl: string | null }>('/api/games/' + id + '/cover/find'),
  mcpInfo: () => get<any>('/api/mcp/info'),
  access: () => get<AccessInfo>('/api/access'),
  live: () => get<LiveStatus>('/api/live'),
  liveStart: (mode: 'live' | 'record') => post<LiveStatus>('/api/live/' + mode + '/start'),
  liveStop: () => post<LiveStatus>('/api/live/stop'),
  recordings: () => get<Recording[]>('/api/recordings'),
  deleteRecording: (name: string) => request<{ ok: boolean }>('DELETE', '/api/recordings/' + encodeURIComponent(name)),

  vision: () => get<any>('/api/vision'),
  visionStart: (goal: string, port: number) => post<any>('/api/vision/start', { goal, port, confirm: true }),
  visionStop: () => post<any>('/api/vision/stop'),
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export interface AccessUrl { url: string; kind: 'custom' | 'secure' | 'lan' | 'tailscale' | 'hostname' | 'other'; label: string; primary: boolean }
export interface AccessInfo { port: number; urls: AccessUrl[]; localOnly: boolean; viewingFrom: string | null }

export interface SaveInfo { exists: boolean; size?: number; savedAt?: number; device?: string; kind?: 'auto' | 'manual'; hasThumb?: boolean }

export interface AuthStatus { enabled: boolean; local: boolean; signedIn: boolean; remoteDevices?: number }

export interface SmartStep { key: string; code?: string; keyCode?: number; at?: number; shift?: boolean }
