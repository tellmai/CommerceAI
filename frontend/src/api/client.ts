import { ref } from 'vue'
import type { components } from './schema'

export type Entity = Omit<components['schemas']['EntityOut'], 'data'> & { data: Record<string, any> }
export const space = ref(localStorage.getItem('commerceai-space') || 'demo')
export const refresh = ref(0)
export const meta = ref<any>(null)
export const sessionReady = ref(false)
export class ApiError extends Error {
  constructor(message: string, public code: string, public details: any, public status: number) { super(message) }
}
export async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('X-Workspace', space.value)
  const csrf = document.cookie.split('; ').find(x => x.startsWith('commerceai_csrf='))?.split('=').slice(1).join('=')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'include' })
  if (!response.ok) {
    let data: any = {}
    try { data = await response.json() } catch { /* proxy/network failure */ }
    if (response.status === 401) sessionReady.value = false
    throw new ApiError(data.message || '服务暂时不可用，请稍后重试', data.code || 'HTTP_ERROR', data.details, response.status)
  }
  return response.json() as Promise<T>
}
export const post = <T = any>(path: string, payload: any = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(payload) })
export const put = (path: string, payload: any) => request(path, { method: 'PUT', body: JSON.stringify(payload) })
export async function download(path: string, filename: string) {
  const headers = { 'X-Workspace': space.value }
  const response = await fetch('/api/v1' + path, { headers, credentials: 'include' })
  if (!response.ok) throw new Error('下载失败，请刷新后重试')
  const url = URL.createObjectURL(await response.blob())
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
export const editor = ref<{ kind: string, row?: Entity, values?: Record<string, any> } | null>(null)
export function openEditor(kind: string, values: Record<string, any> = {}, row?: Entity) { editor.value = { kind, values, row } }
export const referenceKinds: Record<string, string> = {
  source_id: 'sources', evidence_id: 'evidence', product_id: 'products', sku_id: 'skus', listing_id: 'listings', supplier_id: 'suppliers',
  offer_id: 'offers', candidate_id: 'candidates', keyword_id: 'keywords', experiment_id: 'experiments', primary_offer_id: 'offers', backup_offer_id: 'offers',
  forecast_id: 'forecasts', market_report_id: 'market/reports',
}
export function entityName(row: Entity) {
  return row.data._label || row.data.name || row.data.keyword || row.data.title || row.data.locator || row.data.hypothesis || row.data.query ||
    (row.data.unit_cost ? `报价 ¥${row.data.unit_cost} · ${row.data.region}` : '') || row.data.reason || row.data.note || row.id.slice(0, 8)
}
export function pretty(value: any): string {
  if (value === null || value === undefined || value === '') return '待核验'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
export function amount(value: any): string { return value === null || value === undefined ? '—' : Number(value).toFixed(2) }
