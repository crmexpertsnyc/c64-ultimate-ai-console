export type CapState = 'supported' | 'unsupported' | 'unverified' | 'unknown'

export interface CapabilityDetail {
  label: string
  state: CapState
  evidence: string
  checked_at: number
}

export interface Capabilities {
  firmware: string
  product: string
  apiVersion: string
  reachable: boolean
  passwordRequired: boolean
  authOk: boolean
  probedAt: number
  inputMode: 'rest' | 'legacy' | 'none'
  capabilities: Record<string, boolean>
  usable: Record<string, boolean>
  details: Record<string, CapabilityDetail>
}

export interface DeviceInfo {
  product: string
  firmwareVersion: string
  fpgaVersion: string
  coreVersion: string
  hostname: string
  uniqueId: string
}

export interface Drive {
  id: string
  enabled: boolean
  busId: number | null
  type: string
  rom: string
  imageFile: string
  imagePath: string
  mounted: boolean
  lastError: string
}

export interface HeldInput {
  key: string
  kind: string
  inputs: string[]
  port: number | null
  heldFor: number
}

export interface InputStatus {
  mode: 'rest' | 'legacy' | 'none'
  joystickPort: number
  joystickSupported: boolean
  joystickVia?: 'bridge' | 'rest' | null
  bridge?: JoyBridgeStatus | null
  keyHoldSupported: boolean
  held: HeldInput[]
}

export interface SessionDisk {
  mediaId: number
  diskNumber: number
  label: string | null
  path: string
  storage: string
}

export interface Session {
  gameId: number | null
  title: string | null
  format: string | null
  disks: SessionDisk[]
  currentDisk: number | null
  drive: string
  joystickPort: number | null
  startedAt: number | null
  diskCount: number
}

export interface StreamState {
  active: boolean
  target: string | null
  bound: boolean
  frames?: number
  packets: number
  error: string | null
  lastFrameAge?: number | null
  sampleRate?: number
  listeners?: number
}

export interface DeviceStatus {
  configured: boolean
  simulated: boolean
  simulatorProfile: string | null
  connected: boolean
  host: string
  baseUrl: string | null
  lastError: string | null
  lastContact: number | null
  apiVersion: string
  info: DeviceInfo | null
  inputMode: 'rest' | 'legacy' | 'none'
  input: InputStatus
  drives: Drive[]
  capabilities: Capabilities
  streams: { video: StreamState; audio: StreamState }
  session: Session | null
}

export interface Media {
  id: number
  path: string
  storage: string
  format: string
  diskNumber: number
  label: string | null
  size: number
  missing: boolean
  info: Record<string, any>
}

export interface GameDetails {
  description: string | null
  tags: string[]
  filled: string[]
  at: number
  webSearch: boolean
  model: string | null
  sources: { n: number; title: string; url: string }[]
}

export interface Game {
  id: number
  title: string
  details?: GameDetails | null
  alternateNames: string[]
  publisher: string | null
  year: number | null
  genre: string | null
  category: string
  format: string
  numDisks: number
  joystickPort: number | null
  players: string | null
  preferredLaunch: string
  loadCommand: string | null
  runAfterLoad: boolean
  resetBeforeLoad: boolean
  startupDelay: number
  loadTimeout: number
  needsFire: boolean
  notes: string | null
  tags: string[]
  coverUrl: string | null
  favorite: boolean
  lastPlayed: string | null
  playCount: number
  sourceRoot: string | null
  media?: Media[]
}

export interface LaunchStep {
  name: string
  status: 'pending' | 'running' | 'ok' | 'skipped' | 'failed'
  detail: string
  at: number
}

export interface LaunchJob {
  id: string
  gameId: number
  title: string
  method: string
  status: 'running' | 'done' | 'failed'
  steps: LaunchStep[]
  error: string | null
  startedAt: number
  finishedAt: number | null
}

export interface AuditEntry {
  id: number
  timestamp: string
  source: string
  userCommand: string | null
  intent: Record<string, any> | null
  operation: string
  apiCalls: { operation: string; method: string; path: string; status: number; elapsed_ms: number; ok: boolean }[]
  deviceResponse: string | null
  success: boolean
  durationMs: number
  error: string | null
}

export interface CatalogItem {
  id: string
  category: number
  name: string
  group: string | null
  year: number | null
  source: string
  kind: string
  released?: string | null
  gameId?: number | null
}

export interface CommandResult {
  ok: boolean
  message: string
  intent: Record<string, any> | null
  data: any
  needsConfirmation: boolean
  candidates: Game[]
  onlineCandidates: CatalogItem[]
  interpretation: Record<string, any> | null
}

export interface MenuRow {
  index: number
  text: string
  selected: boolean
  cells?: [string, number, number, number][]
}

export interface MenuScreen {
  encoding: string
  hash: string
  text: string
  title: string
  selectedRow: number | null
  selectedText: string | null
  background: number
  palette: string[]
  rows: MenuRow[]
}

export interface Settings {
  [key: string]: any
}

export interface Screenshot {
  name: string
  url: string
  size: number
  title: string | null
  gameId: number | null
  takenAt: number
  auto: boolean
}

export interface Recording {
  name: string
  url: string
  size: number
  createdAt: number
}

export interface LiveStatus {
  running: boolean
  mode: 'live' | 'record' | null
  target: string
  startedAt: number | null
  seconds: number
  stats: Record<string, string>
  error: string | null
  ffmpeg: boolean
  rtmpUrl: string
  streamKeySet: boolean
  bitrateKbps: number
  presets: Record<string, string>
  file: string | null
}

export interface CoverState {
  running: boolean
  total: number
  done: number
  found: number
}

export interface JoyBridgeStatus {
  configured: boolean
  host: string
  port: number
  online: boolean
  ports: number[]
  name: string | null
  firmware: string | null
  rssi: number | null
  uptime: number | null
  rttMs: number | null
  held: Record<string, string[]>
}
