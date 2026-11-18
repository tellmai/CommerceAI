export const resources: Record<string, { title: string, subtitle: string, columns: string[], immutable?: boolean }> = {
  sources: { title: '数据来源', subtitle: '记录使用权限、来源范围和有效期', columns: ['name', 'platform', 'permission_scope', 'enabled', 'expires_at'] },
  evidence: { title: '证据档案', subtitle: '保留来源、采集时间和核验依据', columns: ['locator', 'source_id', 'observed_at', 'quality_flag', 'precision'] },
  keywords: { title: '关键词研究', subtitle: '在相同口径与完整窗口内寻找机会', columns: ['keyword', 'category', 'search_index', 'listing_count', 'conversion', 'window_end'] },
  products: { title: '商品档案', subtitle: '维护商品及其标准规格', columns: ['name', 'category', 'brand'] },
  skus: { title: '标准 SKU', subtitle: '同规格比价与供货审核的共同依据', columns: ['name', 'product_id', 'spec_key', 'quantity', 'attributes'] },
  listings: { title: '平台商品', subtitle: '登记平台商品链接及实际销售规格', columns: ['title', 'platform', 'external_id', 'quantity', 'attributes'] },
  matches: { title: '同款审核', subtitle: '核对数量、材质及尺寸后确认同款', columns: ['listing_id', 'sku_id', 'state', 'reason'] },
  prices: { title: '零售价格', subtitle: '标价、优惠资格、运费和时间分别记录', columns: ['listing_id', 'price_kind', 'price', 'shipping', 'region', 'observed_at'] },
  suppliers: { title: '供应商', subtitle: '维护主备货源和供应商档案', columns: ['name', 'platform', 'external_id', 'note'] },
  offers: { title: '代发报价', subtitle: '核验实际 SKU、费用、时效及退换条件', columns: ['supplier_id', 'sku_id', 'unit_cost', 'shipping', 'lead_hours', 'verified', 'expires_at'] },
  samples: { title: '样品检查', subtitle: '将质量和履约结果作为测款前提', columns: ['offer_id', 'result', 'reason', 'evidence_id'], immutable: true },
  candidates: { title: '候选商品', subtitle: '把值得研究的机会推进到可验证的经营实验', columns: ['sku_id', 'state', 'primary_offer_id', 'keyword_id', 'note'] },
  profits: { title: '利润情景', subtitle: '保存贡献利润、退款假设及获客成本', columns: ['candidate_id', 'label', 'p', 'c', 'a', 'r'], immutable: true },
  assessments: { title: '研究评估', subtitle: '人工分项评分必须有理由和证据', columns: ['candidate_id', 'fulfillment_score', 'quality_score', 'differentiation_score', 'reason'], immutable: true },
  risks: { title: '风险审核', subtitle: '严重风险优先于评分，解除风险需要新证据', columns: ['candidate_id', 'risk_code', 'status', 'reason'], immutable: true },
  strategies: { title: '策略版本', subtitle: '参数变更保存为新版本，历史结果保留原配置', columns: ['name', 'version'], immutable: true },
  experiments: { title: '测款计划', subtitle: '明确假设、预算、观察期限和停止条件', columns: ['candidate_id', 'status', 'budget', 'start_at', 'end_at', 'hypothesis'] },
  funnels: { title: '渠道漏斗', subtitle: '按日期和渠道记录流量、成交及广告支出', columns: ['business_date', 'channel', 'impressions', 'clicks', 'paid_orders', 'ad_spend'] },
  orders: { title: '订单账本', subtitle: '费用未知保持为空，成熟且费用完整后计算实际贡献', columns: ['external_order_id', 'paid_at', 'revenue', 'customer_refund', 'cost_complete', 'mature', 'settled'] },
  transactions: { title: '公开历史交易', subtitle: '保留 GBP 原币种，无法匹配的取消记录单独标记', columns: ['invoice_no', 'description', 'quantity', 'unit_price', 'currency', 'invoice_at', 'cancellation_status'] },
  signals: { title: '销售热度信号', subtitle: '保留已售、已拼、评价的原文与口径，不能直接相加', columns: ['listing_id','metric_kind','raw_value','precision','unit','window_end'] },
}
export const labels: Record<string, string> = {
  id: '记录', version: '版本', name: '名称', title: '商品标题', category: '类目', brand: '品牌', platform: '平台',
  permission_scope: '允许的数据类型', permission_note: '使用权限说明', enabled: '启用来源', expires_at: '有效期至', license: '数据许可', origin_url: '原始出处',
  locator: '证据位置或链接', observed_at: '采集时间', quality_flag: '证据状态', precision: '指标精度', note: '备注',
  source_id: '数据来源', evidence_id: '核验证据', keyword: '关键词', window_start: '窗口开始', window_end: '窗口结束',
  metric_definition: '指标口径', search_index: '搜索人气', listing_count: '在线商品数', conversion: '支付转化率', mall_click_share: '商城点击占比',
  product_id: '所属商品', sku_id: '标准 SKU', spec_key: '规格标识', quantity: '包装件数', attributes: '规格属性',
  external_id: '平台商品或供应商 ID', external_sku_id: '平台 SKU ID', url: '商品链接', listing_id: '平台商品',
  reason: '核验理由', state: '当前阶段', price_kind: '价格口径', currency: '币种', region: '适用地区', price: '商品价格', shipping: '运费',
  conditions: '优惠条件', eligibility_verified: '优惠资格已核验', supplier_id: '供应商', dropship: '支持一件代发',
  unit_cost: '代发采购价', service_fee: '包材与代发服务费', lead_hours: '承诺发货小时', return_terms: '退换货约定', verified: '报价已核验',
  offer_id: '供货报价', result: '样品结果', candidate_id: '候选商品', keyword_id: '关联关键词', primary_offer_id: '主供报价', backup_offer_id: '备供报价',
  p: '实付收入 P', c: '采购成本 C', f: '出库运费 F', b: '包材及代发 B', t: '平台费及变动税费 T', a: '每支付订单广告费 A', r: '全退概率 r', l: '全退净损失 L', label: '情景类型',
  fulfillment_score: '履约评分', quality_score: '质量与样品评分', backup_score: '主备货源评分', usage_risk_score: '使用与质量安全评分', differentiation_score: '差异化评分',
  head_share: '前五同类链接占比', head_share_sample_count: '同口径集中度样本数', head_share_precision: '集中度精度', distinct_reviews: '去重评论数',
  pain_verified: '痛点已核验', pain_solvable: '痛点可解决', bundle_fulfillment_verified: '组合履约已核验', single_contribution: '单品贡献', bundle_contribution: '组合贡献',
  risk_code: '审核类别', status: '状态', config: '策略参数', start_at: '开始时间', end_at: '结束时间', budget: '广告预算', loss_limit: '可承受亏损', target_clicks: '目标有效点击', hypothesis: '测款假设',
  experiment_id: '测款计划', business_date: '业务日期', channel: '渠道', impressions: '曝光', clicks: '有效点击', paid_orders: '支付订单数', ad_spend: '广告支出',
  external_order_id: '外部订单标识', line_id: '订单明细标识', paid_at: '支付时间', revenue: '实付收入', customer_refund: '客户退款', purchase: '采购支出', purchase_refund: '采购退款',
  packaging: '包材与服务费', net_fees: '净平台费与变动税费', advertising: '已分摊广告费', other_net_cost: '其他变动费用', mature: '售后已成熟', settled: '费用已结算', cost_complete: '费用完整',
  invoice_no: '发票编号', stock_code: '原商品编码', description: '原始商品描述', invoice_at: '原交易时间', unit_price: '单价 GBP', country: '国家', source_row: '原文件行号', cancellation_status: '取消记录状态',
  demand: '市场需求', competition: '市场竞争', profit: '盈利能力', supply: '供应链', growth: '市场增长', aftersale: '售后风险', differentiation: '差异化',
  metric_kind:'信号类型',unit:'统计单位',raw_value:'显示原文',value:'解析数值',upper_value:'区间上界',evidence_version:'证据版本',contribution:'实际贡献',offer_version:'计算时主供报价版本',
}
export const states: Record<string, string> = {
  DISCOVERED: '已发现', RESEARCHING: '研究中', READY_SAMPLE: '待买样', SAMPLE_PASSED: '样品通过', READY_TEST: '可测款', TESTING: '测款中', SCALE: '建议扩大', HOLD: '已暂停', REJECTED: '已淘汰', ARCHIVED: '已归档',
  PASS: '通过', FAIL: '未命中', UNKNOWN: '待核验', BLOCKED: '已阻断', NEEDS_EVIDENCE: '待补证据', PENDING: '待核验', MATCHED: '已确认同款', ALTERNATIVE: '替代品', PASSED: '合格', FAILED: '失败',
  NORMAL: '正常情景', STRESS: '压力情景', LIST: '标价', ELIGIBLE: '优惠可得价', DROPSHIP: '代发价', WHOLESALE: '批发价',
  IP: '知识产权', QUALIFICATION: '资质', SAFETY: '商品安全', FULFILLMENT: '履约', PLANNED: '待开始', RUNNING: '进行中', PAUSED: '已暂停', CLOSED: '已结束',
  RESEARCH_REQUIRED: '需重新研究', INCONCLUSIVE: '样本不足', REVIEW_SCALE: '可审核扩大', OPTIMIZE: '建议优化', PAUSE: '建议暂停',
  QUEUED: '排队中', SUCCEEDED: '已完成', CANCELLED: '已取消', PREVIEW: '待确认', VALIDATING: '校验中', IMPORTING: '写入中',
  valid: '有效', conflict: '有冲突', rejected: '已拒绝', exact: '精确值', lower_bound: '下界值', range: '区间值', estimate: '估算值', unknown: '未知',
  UNMATCHED: '取消记录未匹配', SALE: '销售记录', LOW: '低', MEDIUM: '中', HIGH: '高',
  blue_ocean: '蓝海长尾', price_gap: '跨平台价差', pain_point: '差评痛点', bundle: '关联组合',
  growth: '趋势增长',
  SOLD:'已售',GROUP_BUY:'已拼',REVIEW_COUNT:'评价数',ESTIMATED_SALES:'估算销量',ORDERS:'订单',ITEMS:'件数',REVIEWS:'条评价',
}
export function stateLabel(value: any) { return states[value] || String(value ?? '待核验') }
export function tagType(value: any): 'success' | 'warning' | 'danger' | 'info' {
  if (['BLOCKED', 'FAILED', 'REJECTED', 'FAIL'].includes(value)) return 'danger'
  if (['PASS', 'PASSED', 'MATCHED', 'SUCCEEDED', 'READY_TEST', 'TESTING', 'SCALE'].includes(value)) return 'success'
  if (['UNKNOWN', 'PENDING', 'NEEDS_EVIDENCE', 'HOLD', 'UNMATCHED'].includes(value)) return 'warning'
  return 'info'
}
export const transitions: Record<string, string[]> = {
  DISCOVERED:['RESEARCHING','HOLD','REJECTED'], RESEARCHING:['READY_SAMPLE','HOLD','REJECTED'], READY_SAMPLE:['SAMPLE_PASSED','RESEARCHING','HOLD','REJECTED'],
  SAMPLE_PASSED:['READY_TEST','RESEARCHING','HOLD','REJECTED'], READY_TEST:['TESTING','RESEARCHING','HOLD','REJECTED'], TESTING:['SCALE','HOLD','REJECTED'],
  SCALE:['HOLD','REJECTED'], HOLD:['RESEARCHING','REJECTED','ARCHIVED'], REJECTED:['RESEARCHING','ARCHIVED'], ARCHIVED:[],
}
