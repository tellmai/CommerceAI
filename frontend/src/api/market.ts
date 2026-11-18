import type { components } from './schema'
import type { Entity } from './client'

export type Platform = '1688' | 'taobao' | 'jd' | 'pinduoduo'
export const platformNames: Record<Platform, string> = {
  '1688': '1688货源', taobao: '淘宝', jd: '京东', pinduoduo: '拼多多',
}
export type SearchInput = components['schemas']['SearchInput']
export type ManualQuote = components['schemas']['ManualQuote']
export type CompareInput = components['schemas']['CompareInput']
export type ConnectionInput = components['schemas']['ConnectionInput']
type MarketMode = 'LIVE' | 'DEMO' | 'MANUAL'
export const marketModeNames: Record<MarketMode, string> = { LIVE: '联网查询', DEMO: '模拟演练', MANUAL: '人工录入' }
export interface MarketVariant {
  id: string; label: string; price: string | null; attributes: Record<string, string>
  pack_count: number | null; warning?: string | null
}
export interface MarketItem {
  id: string; external_id: string; platform: Platform; title: string; url: string
  price: string | null; promotion_price: string | null; currency: string; currency_inferred: boolean
  display_price?: string
  pack_count: number | null; attributes: Record<string, string>; variants: MarketVariant[]
  shipping: string | null; shipping_region: string; min_order: number | null
  price_tiers: { min_quantity: number; price: string }[]; seller: string; seller_id: string
  dropship: boolean | null; observed_at: string; source_updated_at: string | null
  detail_loaded: boolean; warnings: string[]; evidence_note?: string
}
export interface MarketConnection {
  provider: string; provider_name: string; enabled: boolean; configured: boolean
  has_secret: boolean; has_access_token: boolean; platforms: Platform[]
  taobao_route: 'tglobal' | 'taobao'; permission_note: string; version: string; docs_url: string; notice: string
}
export type MarketRun = Omit<Entity, 'data'> & {
  data: {
    request: {query: string; mode: MarketMode; platforms: Platform[]}; status: string; items: MarketItem[]; calls_reserved: number; call_limit: number
    errors: { platform: Platform; code: string; message: string }[]
    provider_version: string; created_at: string
  }
  job?: { id: string; status: string; progress: number; error: string | null }
}
export type MarketReport = Omit<Entity, 'data'> & {
  data: {
    query: string; mode: MarketMode; market_run_id: string; provider_version: string; inputs: CompareInput
    supply: MarketItem; retail: MarketItem; created_at: string; candidate_id?: string
    result: {
      recommendation: string; match_state: string; sticker_spread: string | null
      normal: { display_contribution: string; contribution_rate: number | null; max_cac: string } | null
      stress: { display_contribution: string } | null
      procurement_cost_per_sale_pack: string | null; retail_total: string | null; initial_cash: string | null
      blocked: string[]; pending: string[]; cautions: string[]; conflicts: string[]
      missing_attributes: string[]; supply_unit_price: string | null; retail_unit_price: string | null
      cash_scope: string; min_order: number | null
    }
  }
}
export const recommendationNames: Record<string, string> = {
  NOT_RECOMMENDED: '暂不建议采购', NEEDS_VERIFICATION: '需要补充核验', REVIEW_SAMPLE: '可考虑买样验证', DEMO_ONLY: '模拟分析结果',
}
export function platformSearch(platform: Platform, query: string) {
  const q = encodeURIComponent(query.trim())
  return {
    '1688': `https://s.1688.com/selloffer/offer_search.htm?keywords=${q}`,
    taobao: `https://s.taobao.com/search?q=${q}`,
    jd: `https://search.jd.com/Search?keyword=${q}`,
    pinduoduo: `https://mobile.yangkeduo.com/search_result.html?search_key=${q}`,
  }[platform]
}
