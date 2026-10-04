import { get, post, qs, request } from './api'
import type { NewsItem } from '../pages/NewsPage'

export type EventSource = 'csdb' | 'web' | 'user'

export interface RetroEvent {
  id: number
  source: EventSource
  sourceLabel: string
  name: string
  type: string | null
  start: string            // YYYY-MM-DD
  end: string              // YYYY-MM-DD, inclusive
  city: string | null
  country: string | null
  region: string | null    // US state / Canadian province code
  regionName: string | null
  flag: string
  scope: 'commodore' | 'retro'
  home: boolean            // in your home country (Settings → events home country)
  url: string | null       // the event's website
  pageUrl: string | null   // CSDb page / the web page it was found on
  streamUrl: string | null
  image: string | null
  tagline: string | null
  notes: string | null
  hidden: boolean
  live: boolean            // happening today (a party, not a months-long compo)
  ongoing: boolean
  editable: boolean
  ics: string
}

export interface EventsState {
  running: boolean
  researching: boolean
  csdbAt: string | null
  researchAt: string | null
  errors: Record<string, string>
  aiConfigured: boolean
  webSearch: boolean
  csdbEveryHours: number
}

export interface EventList {
  items: RetroEvent[]
  live: RetroEvent[]
  liveVideos: NewsItem[]
  countries: string[]
  homeCountry: string
  homeCount: number
  regionCountry: string
  regions: { code: string; name: string }[]
  types: string[]
  hiddenCount: number
  state: EventsState
}

export interface EventQuery {
  country?: string
  type?: string
  q?: string
  include_past?: boolean
  include_hidden?: boolean
  from?: string
  to?: string
  region?: string
  scope?: 'commodore' | 'retro'
  home_first?: boolean
}

export interface EventInput {
  name: string
  start: string
  end?: string | null
  city?: string | null
  country?: string | null
  url?: string | null
  type?: string | null
  notes?: string | null
}

export interface FirmwareStatus {
  product: string
  installed: string | null
  latest: string | null
  newer: boolean
  downloadPage: string
  changelogUrl: string | null
  checkedAt: string | null
  source: 'commodore' | 'ultimate64'
  sourceLabel: string
  error: string | null
  note: string
}

export const eventsApi = {
  list: (q: EventQuery = {}) => get<EventList>(`/api/events${qs(q as Record<string, unknown>)}`),
  refresh: () => post<{ added: number; updated: number; errors: Record<string, string>; checkedAt: string }>('/api/events/refresh'),
  research: () => post<{ added: number; updated: number; found: number; dropped: number; errors: string[] }>('/api/events/research'),
  add: (e: EventInput) => post<RetroEvent>('/api/events', e),
  edit: (id: number, changes: Partial<EventInput> & { hidden?: boolean }) => request<RetroEvent>('PATCH', `/api/events/${id}`, changes),
  remove: (id: number) => request<{ deleted: number }>('DELETE', `/api/events/${id}`),
  icsUrl: (id: number) => `/api/events/${id}.ics`,
  calendarUrl: () => `${window.location.origin}/api/events/calendar.ics`,
  firmware: () => get<FirmwareStatus>('/api/firmware'),
  checkFirmware: () => post<FirmwareStatus>('/api/firmware/check'),
}
