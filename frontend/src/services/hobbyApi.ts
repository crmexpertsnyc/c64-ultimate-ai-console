import { get, post, qs, request } from './api'
import type { NewsItem } from '../pages/NewsPage'

// 🛒 shop ----------------------------------------------------------------
export interface ShopLink { seller: string; sellerName: string; url: string; affiliate: boolean; price: number | null; currency: string | null }
export interface ShopItem { id: string; name: string; description: string; category: string; tags: string[]; image: string | null; imageUrl: string | null; links: ShopLink[] }
export interface ShopService { name: string; url: string; region: string; tags: string[]; note: string }
export interface Catalog {
  version: string; updated: string; disclosure: string; source: 'built-in' | 'website'; error: string | null
  categories: Record<string, string>; items: ShopItem[]; services: ShopService[]
  sellers: { id: string; name: string; url: string | null; country: string; affiliate: boolean }[]
  ebay: { configured: boolean; affiliate: boolean }
}
export interface Suggestion { why: string; tags: string[]; items: ShopItem[] }
export interface Listing { id: string; title: string; price: number; currency: string; condition: string | null; image: string | null; url: string; affiliate: boolean; seller: string | null; location: string | null }
export interface Watch { id: number; query: string; maxPrice: number | null; currency: string; active: boolean; lowest: number | null; collectionItemId: number | null; checkedAt: string | null }

// 🔧 repair --------------------------------------------------------------
export interface Machine { id: string; name: string; original: boolean; note: string }
export interface Cause { ref: string; title: string; likelihood: 'high' | 'medium' | 'low'; why: string; aiWhy?: string | null; checks: string[]; tags: string[]; symptom: string }
export interface Reference { title: string; url: string; kind: string }
export interface Diagnosis {
  machine: Machine; symptoms: string[]; summary: string | null; questions: string[]; extraChecks: string[]
  ai: boolean; aiError: string | null; causes: Cause[]; safety: { id: string; text: string }[]
  parts: ShopItem[]; references: Reference[]; services: ShopService[]
  videos: { items: NewsItem[]; searchUrl: string; query: string }
}

// 📦 collection ----------------------------------------------------------
export interface CollectionItem {
  id: number; kind: string; title: string; platform: string; edition: string | null; condition: string | null
  boxed: boolean; complete: boolean; quantity: number; serial: string | null; notes: string | null; location: string | null
  purchasePrice: number | null; purchaseDate: string | null; value: number | null; currency: string
  valueSource: string | null; valueAt: string | null; gameId: number | null; coverUrl: string | null
  wishlist: boolean; targetPrice: number | null; createdAt: string | null
}
export interface CollectionList {
  items: CollectionItem[]; kinds: Record<string, string>; conditions: Record<string, string>
  summary: { owned: number; valued: number; totals: Record<string, number> }
}
export type ItemInput = Partial<{
  kind: string; title: string; platform: string; edition: string | null; condition: string | null; boxed: boolean
  complete: boolean; quantity: number; serial: string | null; notes: string | null; location: string | null
  purchase_price: number | null; purchase_date: string | null; value: number | null; currency: string
  game_id: number | null; wishlist: boolean; target_price: number | null
}>

export const hobbyApi = {
  catalog: (p: { category?: string; tag?: string; q?: string } = {}) => get<Catalog>(`/api/shop/catalog${qs(p)}`),
  refreshCatalog: () => post<{ source: string; error: string | null; items: number }>('/api/shop/catalog/refresh'),
  suggest: (p: { context: 'game' | 'controller' | 'repair' | 'setup'; game_id?: number; using?: string; tags?: string }) =>
    get<{ context: string; suggestions: Suggestion[]; services: ShopService[] }>(`/api/shop/suggest${qs(p)}`),
  advisor: (prompt: string, budget?: number) =>
    post<{ intro: string; kit: { item: ShopItem; why: string; priority: 'must' | 'nice' }[]; tips: string[] }>('/api/shop/advisor', { prompt, budget }),
  ebay: (q: string, maxPrice?: number) =>
    get<{ configured: boolean; searchUrl: string; soldUrl: string; items: Listing[] }>(`/api/shop/ebay${qs({ q, max_price: maxPrice })}`),
  watches: () => get<{ watches: Watch[]; deals: NewsItem[]; ebayConfigured: boolean }>('/api/shop/watches'),
  addWatch: (query: string, maxPrice?: number | null) => post<Watch>('/api/shop/watches', { query, max_price: maxPrice ?? null }),
  removeWatch: (id: number) => request<{ ok: boolean }>('DELETE', `/api/shop/watches/${id}`),
  checkWatches: () => post<{ found: number }>('/api/shop/watches/check'),

  repairKb: () => get<{ machines: Machine[]; symptoms: { id: string; title: string; machines: string[] }[]; references: Reference[] }>('/api/repair/kb'),
  diagnose: (machine: string, text: string, symptom?: string) => post<Diagnosis>('/api/repair/diagnose', { machine, text, symptom }),

  collection: (p: { kind?: string; q?: string; wishlist?: boolean } = {}) =>
    get<CollectionList>(`/api/collection${qs({ ...p, wishlist: p.wishlist === undefined ? undefined : String(p.wishlist) })}`),
  addItem: (data: ItemInput) => post<CollectionItem>('/api/collection', data),
  addFromLibrary: (gameId: number, data: ItemInput) => post<CollectionItem>(`/api/collection/from-library/${gameId}`, data),
  updateItem: (id: number, data: ItemInput) => request<CollectionItem>('PATCH', `/api/collection/${id}`, data),
  deleteItem: (id: number) => request<{ ok: boolean }>('DELETE', `/api/collection/${id}`),
  estimate: (id: number) => post<{ value: number | null; currency: string | null; count: number; basis: string | null; soldUrl: string; configured: boolean; item: CollectionItem }>(`/api/collection/${id}/estimate`),
}

export function money(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined) return ''
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD', maximumFractionDigits: v < 100 ? 2 : 0 }).format(v) } catch { return `${v} ${currency ?? ''}` }
}
