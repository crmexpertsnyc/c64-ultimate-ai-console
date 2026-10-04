/** 🎵 SID jukebox API: HVSC search, play on the C64's SID chip, 🤖 stations, history. */
import { get, post, qs, request } from './api'

export interface Tune {
  id: string
  category: number
  title: string
  composer: string | null
  group?: string | null
  year?: number | null
  source: string | null
  rating?: number
}

export interface StationTrack extends Tune {
  durationS: number
}

export interface NowPlaying {
  id: string
  category: number
  title: string
  composer: string | null
  released: string | null
  songs: number
  startSong: number
  song: number
  durationS: number
  stationId: number | null
}

export interface SidInfo { title: string; author: string; released: string; songs: number; startSong: number; format: string }

export interface PlayResult { ok: boolean; track: NowPlaying; sid: SidInfo }

export interface StationSummary {
  id: number
  name: string
  prompt: string
  description: string | null
  trackCount: number
  createdAt: string | null
}

export interface Station extends StationSummary {
  tracks: StationTrack[]
  dropped?: number
}

export interface HistoryPlay {
  id: string
  category: number
  title: string
  composer: string | null
  released: string | null
  song: number | null
  songs: number | null
  stationId: number | null
  playedAt: string | null
}

export interface JukeboxStatus { connected: boolean; sidPlayback: boolean; catalog: boolean; nowPlaying: NowPlaying | null }

export interface PlayRequest { id: string; category: number; title?: string; composer?: string | null; song?: number; stationId?: number }

export const jukeboxApi = {
  status: () => get<JukeboxStatus>('/api/jukebox/status'),
  search: (q: string, composer: string) => get<{ tunes: Tune[] }>(`/api/jukebox/search${qs({ q, composer })}`),
  play: (body: PlayRequest) => post<PlayResult>('/api/jukebox/play', body),
  stop: () => post<{ ok: boolean; reset: boolean }>('/api/jukebox/stop'),
  history: () => get<{ plays: HistoryPlay[] }>('/api/jukebox/history'),
  stations: () => get<{ stations: StationSummary[]; suggestions: string[] }>('/api/jukebox/stations'),
  station: (id: number) => get<Station>(`/api/jukebox/stations/${id}`),
  makeStation: (prompt: string) => post<Station>('/api/jukebox/stations', { prompt }),
  deleteStation: (id: number) => request<{ ok: boolean }>('DELETE', `/api/jukebox/stations/${id}`),
}
