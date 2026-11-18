<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { amount, download, post, request, space } from '../api/client'
import { marketModeNames, platformNames, platformSearch, recommendationNames, type CompareInput, type MarketConnection,
  type MarketItem, type MarketReport, type MarketRun, type Platform } from '../api/market'
import MarketConnectionEditor from '../components/MarketConnection.vue'
import MarketManualQuotes from '../components/MarketManualQuotes.vue'
import MarketQuotes from '../components/MarketQuotes.vue'

const router = useRouter(), route = useRoute()
const connection = ref<MarketConnection | null>(null), connectionOpen = ref(false), manualOpen = ref(false)
const query = ref('牙刷'), perPlatform = ref(3), platforms = ref<Platform[]>(Object.keys(platformNames) as Platform[])
const active = ref<MarketRun | null>(null), runs = ref<MarketRun[]>([]), reports = ref<MarketReport[]>([])
const report = ref<MarketReport | null>(null), batchReports = ref<MarketReport[]>([])
const supplyId = ref(''), retailId = ref(''), supplySku = ref(''), retailSku = ref('')
const supplyAttrs = ref('{}'), retailAttrs = ref('{}'), busy = ref(false), analyzing = ref(false), error = ref('')
const promoteOpen = ref(false), candidateName = ref(''), category = ref('口腔护理')
const values = reactive<Record<string, any>>({ purchase_quantity: 1, procurement_mode: 'STOCK', min_order: null,
  dropship_verified: false, supply_pack: null, retail_pack: null, allow_repack: false, match_confirmed: false,
  verification_reason: '', supply_price: null, retail_price: null, retail_shipping: null, sale_price: null,
  inbound_shipping: null, outbound_shipping: null, packaging: null, fees: null, advertising: null,
  refund_rate: null, refund_loss: null, ad_budget: null, region: '全国' })
const costs: Record<string, string> = {
  sale_price: '你的预计售价（留空采用选中零售SKU价）', retail_shipping: '竞品运费（用于比较竞品到手价）',
  inbound_shipping: '整批进货运费（代发为供应商直发运费）', outbound_shipping: '每销售包装的额外发货运费',
  packaging: '每单包材与服务费', fees: '每单平台费及变动税费', advertising: '每支付订单广告费',
  refund_loss: '每次售后净损失（不含广告）', ad_budget: '本批广告预算（用于现金估算）',
}
const supplyItems = computed(() => active.value?.data.items.filter(i => i.platform === '1688') || [])
const retailItems = computed(() => active.value?.data.items.filter(i => i.platform !== '1688') || [])
const supply = computed(() => supplyItems.value.find(i => i.id === supplyId.value))
const retail = computed(() => retailItems.value.find(i => i.id === retailId.value))
const jobActive = computed(() => ['QUEUED', 'RUNNING'].includes(active.value?.job?.status || ''))
const ready = computed(() => ['SUCCEEDED', 'PARTIAL'].includes(active.value?.data.status || '') && !!supply.value && !!retail.value)
const liveReady = computed(() => connection.value?.configured && connection.value.enabled)
const reportExpired = computed(() => report.value && [report.value.data.supply, report.value.data.retail]
  .some(item => Date.now() - new Date(item.observed_at).getTime() > 864e5))
const reportConnectionChanged = computed(() => report.value?.data.mode === 'LIVE' &&
  (!connection.value?.enabled || report.value.data.provider_version !== connection.value.version))
const statusNames: Record<string, string> = { QUEUED: '排队中', RUNNING: '查询中', SUCCEEDED: '已完成', PARTIAL: '部分完成', FAILED: '失败', CANCELLED: '已取消' }
let timer: ReturnType<typeof setInterval> | undefined, polling = false
let disposed = false

async function loadHistory() {
  const [a, b] = await Promise.all([request<{items:MarketRun[]}>('/market/runs'), request<{items:MarketReport[]}>('/market/reports')])
  runs.value = a.items; reports.value = b.items
}
async function initialize() {
  try {
    connection.value = await request('/market/connection')
    await loadHistory()
    if (typeof route.query.report === 'string') report.value = await request('/market/reports/' + route.query.report)
  } catch (e: any) { error.value = e.message }
}
async function loadRun(id: string, reset = false) {
  if (reset && timer) { clearInterval(timer); timer = undefined }
  try {
    const value = await request<MarketRun>('/market/runs/' + id)
    if (disposed || (!reset && active.value?.id !== id)) return
    active.value = value
    if (reset || !value.data.items.some(i => i.id === supplyId.value)) supplyId.value = value.data.items.find(i => i.platform === '1688')?.id || ''
    if (reset || !value.data.items.some(i => i.id === retailId.value)) retailId.value = value.data.items.find(i => i.platform !== '1688')?.id || ''
    if (reset) updateSelection()
    if (!['QUEUED', 'RUNNING'].includes(value.job?.status || '')) {
      if (timer) clearInterval(timer)
      timer = undefined
      await loadHistory()
    } else if (!timer) {
      timer = setInterval(async () => {
        if (polling) return
        polling = true
        try { await loadRun(id) } finally { polling = false }
      }, 1500)
    }
  } catch (e: any) { error.value = e.message; if (timer) clearInterval(timer); timer = undefined }
}
async function search() {
  busy.value = true; error.value = ''; report.value = null; batchReports.value = []
  try {
    const response = await post<{run: MarketRun; job_id: string}>('/market/runs', {
      query: query.value, per_platform: perPlatform.value, platforms: platforms.value, page: 1,
      mode: space.value === 'demo' ? 'DEMO' : 'LIVE',
    })
    await loadRun(response.run.id, true)
  } catch (e: any) { error.value = e.message }
  finally { busy.value = false }
}
function defaultAttrs(item?: MarketItem, sku = '') {
  const data = item?.variants.find(v => v.id === sku)?.attributes || item?.attributes || {}
  return JSON.stringify({ material: data.material || '', size: data.size || '',
    ...(data.brand ? { brand: data.brand } : {}), ...(data.model ? { model: data.model } : {}) }, null, 2)
}
function updateSelection() {
  supplySku.value = ''; retailSku.value = ''
  values.supply_pack = supply.value?.pack_count ?? null; values.retail_pack = retail.value?.pack_count ?? null
  values.min_order = supply.value?.min_order ?? null
  values.supply_price = null; values.retail_price = null
  values.match_confirmed = false; values.verification_reason = ''
  supplyAttrs.value = defaultAttrs(supply.value); retailAttrs.value = defaultAttrs(retail.value)
  report.value = null; batchReports.value = []
}
watch([supplyId, retailId], updateSelection)
watch(supplySku, value => {
  supplyAttrs.value = defaultAttrs(supply.value, value)
  values.supply_pack = supply.value?.variants.find(v => v.id === value)?.pack_count ?? supply.value?.pack_count ?? null
  values.match_confirmed = false; values.supply_price = null; report.value = null
})
watch(retailSku, value => {
  retailAttrs.value = defaultAttrs(retail.value, value)
  values.retail_pack = retail.value?.variants.find(v => v.id === value)?.pack_count ?? retail.value?.pack_count ?? null
  values.match_confirmed = false; values.retail_price = null; report.value = null
})
function payload(): CompareInput {
  const result = { ...values }
  for (const key of [...Object.keys(costs), 'supply_price', 'retail_price']) {
    result[key] = typeof values[key] === 'string' ? values[key].trim() || null : values[key]
  }
  return { ...result, expected_version: active.value!.version, supply_id: supplyId.value, retail_id: retailId.value,
    supply_sku: supplySku.value, retail_sku: retailSku.value,
    supply_attributes: JSON.parse(supplyAttrs.value), retail_attributes: JSON.parse(retailAttrs.value) } as CompareInput
}
function describeError(e: any) {
  return e.message + (e.details?.conflicts?.length ? '：冲突 ' + e.details.conflicts.join('、') : '') +
    (e.details?.missing?.length ? '；缺失 ' + e.details.missing.join('、') : '') +
    (e.details?.errors?.length ? '：' + e.details.errors.map((issue: {loc: string[]}) => {
      const key = issue.loc.at(-1) || ''
      return costs[key] || ({ supply_price: '采购价补录', retail_price: '竞品价格补录', region: '适用地区',
        verification_reason: '核验理由与价格依据' } as Record<string, string>)[key] || key
    }).join('、') : '')
}
async function compare(all = false) {
  if (!active.value) return
  analyzing.value = true; error.value = ''; batchReports.value = []
  try {
    const input = payload()
    if (all) {
      for (const item of retailItems.value) {
        const selected = item.id === retailId.value
        const row = await post<MarketReport>(`/market/runs/${active.value.id}/compare`, selected ? input : {
          ...input, retail_id: item.id, retail_sku: '', retail_pack: null, retail_attributes: {},
          retail_price: null, retail_shipping: null, match_confirmed: false,
        })
        batchReports.value.push(row)
      }
      report.value = batchReports.value.find(r => r.data.retail.id === retailId.value) || batchReports.value[0] || null
    } else report.value = await post(`/market/runs/${active.value.id}/compare`, input)
    await loadHistory(); ElMessage.success('分析报告已保存，历史结果保留原输入')
  } catch (e: any) { error.value = describeError(e) }
  finally { analyzing.value = false }
}
async function cancel() {
  if (!active.value?.job) return
  try { await post('/jobs/' + active.value.job.id + '/cancel'); await loadRun(active.value.id) }
  catch (e: any) { error.value = e.message }
}
async function promote() {
  if (!report.value) return
  analyzing.value = true
  try {
    const result = await post(`/market/reports/${report.value.id}/candidate`, { name: candidateName.value, category: category.value })
    promoteOpen.value = false; ElMessage.success('已保存为研究候选，请继续补充主备货源、样品和风险审核')
    await router.push('/records/candidates?open=' + result.id)
  } catch (e: any) { error.value = describeError(e); promoteOpen.value = false }
  finally { analyzing.value = false }
}
onMounted(initialize)
onUnmounted(() => { disposed = true; if (timer) clearInterval(timer) })
</script>

<template>
  <section class="page-heading"><div><p class="eyebrow">产品发现</p><h1>找货与比价</h1>
    <p class="muted">从具体规格和到手成本出发，评估一个产品是否值得继续研究。</p></div>
    <el-button @click="connectionOpen = true">平台连接</el-button>
  </section>
  <el-alert v-if="space === 'demo'" title="当前为模拟演练，所有商品和价格均为模拟数据" type="info" :closable="false" show-icon class="mb16"
    description="输入“牙刷”练习四平台对比、规格核验和利润测算。实际选品请切换真实经营空间录入平台报价。" />
  <el-alert v-if="!liveReady" title="四平台自动查询待开通，当前按免费方式使用" type="warning" :closable="false" show-icon class="mb16"
    description="可免费前往1688、淘宝、京东和拼多多检索，并在真实经营空间录入报价。开通数据服务后，在“平台连接”可启用自动搜索和详情查询。" />
  <el-alert v-if="space === 'public'" title="公开历史空间不执行当前行情查询，请切换到模拟演示或真实经营" type="info" :closable="false" class="mb16" />
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16" />

  <section class="panel">
    <el-form label-position="top" @submit.prevent="search">
      <div class="market-query"><el-form-item label="想研究的产品"><el-input v-model="query" size="large" placeholder="例如：成人手动牙刷 2支装" maxlength="100" /></el-form-item>
        <el-form-item label="每平台详情数量"><el-input-number v-model="perPlatform" :min="1" :max="5" :controls="false" /></el-form-item>
        <el-button type="primary" size="large" :icon="Search" :loading="busy || jobActive" native-type="submit"
          :disabled="space === 'public' || (space === 'live' && !liveReady) || !query.trim()">
          {{ space === 'demo' ? '开始模拟分析' : liveReady ? '搜索四平台商品' : '联网查询待开通' }}
        </el-button>
      </div>
      <el-checkbox-group v-model="platforms" class="mb16"><el-checkbox v-for="(name, key) in platformNames" :key="key" :value="key">{{ name }}</el-checkbox></el-checkbox-group>
    </el-form>
    <div class="market-links"><span class="muted">免费人工检索（可能需要平台登录）：</span>
      <a v-for="(name, key) in platformNames" :key="key" :href="platformSearch(key as Platform, query)" target="_blank" rel="noopener noreferrer">前往{{ name }} ↗</a>
      <el-button v-if="space === 'live'" type="primary" :disabled="!query.trim() || jobActive" @click="manualOpen = true">录入平台报价</el-button>
    </div>
    <p v-if="liveReady && space === 'live'" class="help-note">联网查询最多 {{ platforms.length * (perPlatform + 1) }} 次接口请求。保存报告和重新测算使用本地报价快照。</p>
    <template v-if="active?.job"><el-progress :percentage="active.job.progress" :status="active.job.status === 'FAILED' ? 'exception' : undefined" />
      <div class="table-toolbar" style="margin:12px 0 0"><span class="muted">{{ statusNames[active.job.status] }} · 已预留 {{ active.data.calls_reserved }} / {{ active.data.call_limit }} 次外部调用</span>
        <el-button v-if="jobActive" size="small" @click="cancel">取消查询</el-button></div>
    </template>
    <el-alert v-if="active?.job?.status === 'FAILED' && active.job.error" :title="active.job.error" type="error" :closable="false" class="mt24" />
    <el-alert v-for="issue in active?.data.errors || []" :key="issue.platform + issue.code" :title="platformNames[issue.platform] + '：' + issue.message" type="warning" :closable="false" class="mt24" />
  </section>

  <div v-if="active" class="market-quote-grid mt24">
    <section class="panel"><div class="panel-heading"><h3>1688 货源候选</h3><span class="muted">选择一个供货商品</span></div>
      <MarketQuotes :items="supplyItems" :selected="supplyId" supply :loading="jobActive && !supplyItems.length" @select="item => supplyId = item.id" /></section>
    <section class="panel"><div class="panel-heading"><h3>淘宝 / 京东 / 拼多多零售候选</h3><span class="muted">选择要比较的销售规格</span></div>
      <MarketQuotes :items="retailItems" :selected="retailId" :loading="jobActive && !retailItems.length" @select="item => retailId = item.id" /></section>
  </div>

  <section v-if="supply && retail" class="panel mt24">
    <div class="panel-heading"><div><h3>核对规格与成本</h3><p class="muted">{{ supply.title }} → {{ platformNames[retail.platform] }} · {{ retail.title }}</p></div></div>
    <el-alert title="未知费用留空，确认没有发生才填0；标题相似不能直接确认同款" type="info" :closable="false" class="mb16" />
    <el-form label-position="top" class="record-form">
      <el-form-item label="1688具体SKU"><el-select v-model="supplySku" clearable placeholder="无SKU或单一SKU时可留空" style="width:100%">
        <el-option v-for="item in supply.variants" :key="item.id" :value="item.id" :label="item.label + ' · ¥' + amount(item.price)" />
      </el-select></el-form-item>
      <el-form-item label="零售具体SKU"><el-select v-model="retailSku" clearable placeholder="无SKU或单一SKU时可留空" style="width:100%">
        <el-option v-for="item in retail.variants" :key="item.id" :value="item.id" :label="item.label + ' · ¥' + amount(item.price)" />
      </el-select></el-form-item>
      <el-form-item label="货源每包装件数"><el-input-number v-model="values.supply_pack" :min="1" :controls="false" placeholder="未知" /></el-form-item>
      <el-form-item label="零售每包装件数"><el-input-number v-model="values.retail_pack" :min="1" :controls="false" placeholder="未知" /></el-form-item>
      <el-form-item label="货源规格属性"><el-input v-model="supplyAttrs" type="textarea" :rows="4" spellcheck="false" /></el-form-item>
      <el-form-item label="零售规格属性"><el-input v-model="retailAttrs" type="textarea" :rows="4" spellcheck="false" /></el-form-item>
      <el-form-item label="采购方式"><el-select v-model="values.procurement_mode" style="width:100%"><el-option label="小批备货" value="STOCK" /><el-option label="一件代发" value="DROPSHIP" /></el-select></el-form-item>
      <el-form-item label="本批采购包装数量"><el-input-number v-model="values.purchase_quantity" :min="1" :max="1000000" :controls="false" /></el-form-item>
      <el-form-item label="核验后的起订量（包装数）"><el-input-number v-model="values.min_order" :min="1" :controls="false" placeholder="未知" /></el-form-item>
      <el-form-item label="适用地区"><el-input v-model="values.region" /></el-form-item>
      <el-form-item label="采购价补录（每个货源包装）"><el-input v-model="values.supply_price" placeholder="通常留空，采用所选SKU或适用阶梯价" /></el-form-item>
      <el-form-item label="竞品价格补录（每个零售包装）"><el-input v-model="values.retail_price" placeholder="通常留空；补录须在下方注明依据" /></el-form-item>
      <el-form-item v-for="(label, key) in costs" :key="key" :label="label"><el-input v-model="values[key]" placeholder="未知请留空，确认无费用才填0"><template #prefix>¥</template></el-input></el-form-item>
      <el-form-item label="售后损失概率（0–1）"><el-input-number v-model="values.refund_rate" :min="0" :max="1" :step="0.01" :controls="false" placeholder="未知" /></el-form-item>
      <el-form-item label="规格与履约核验" class="full"><div class="market-checks">
        <el-checkbox v-model="values.allow_repack">允许拆包重组（需另行核验包装与履约）</el-checkbox>
        <el-checkbox v-if="values.procurement_mode === 'DROPSHIP'" v-model="values.dropship_verified">已向供应商确认一件代发及费用</el-checkbox>
        <el-checkbox v-model="values.match_confirmed">已核对数量、材质、尺寸、品牌型号、币种及所选SKU价格，确认可比</el-checkbox>
      </div></el-form-item>
      <el-form-item label="核验理由与价格依据" class="full"><el-input v-model="values.verification_reason" type="textarea" :rows="2" placeholder="确认同款至少填写5字；人工价格补录需注明凭据位置和核验情况" /></el-form-item>
    </el-form>
    <div class="form-actions"><span class="muted">压力情景：售价−10%、采购+10%、广告+25%、售后概率+5个百分点、售后净损失+10%。</span>
      <el-button @click="compare(true)" :loading="analyzing" :disabled="!ready">对比全部零售候选</el-button>
      <el-button type="primary" @click="compare(false)" :loading="analyzing" :disabled="!ready">分析所选产品并保存报告</el-button>
    </div>
  </section>

  <section v-if="batchReports.length" class="panel mt24"><div class="panel-heading"><h3>跨平台测算对比</h3><span class="muted">每个报告分别保存，未核验的规格不视为已确认同款</span></div>
    <el-table :data="batchReports"><el-table-column label="平台" width="100"><template #default="{ row }">{{ platformNames[row.data.retail.platform as Platform] }}</template></el-table-column>
      <el-table-column label="商品" min-width="240"><template #default="{ row }">{{ row.data.retail.title }}</template></el-table-column>
      <el-table-column label="折算裸价差"><template #default="{ row }">¥{{ amount(row.data.result.sticker_spread) }}</template></el-table-column>
      <el-table-column label="预计正常贡献"><template #default="{ row }">{{ row.data.result.normal ? '¥' + row.data.result.normal.display_contribution : '费用不完整' }}</template></el-table-column>
      <el-table-column label="结论"><template #default="{ row }">{{ recommendationNames[row.data.result.recommendation] }}</template></el-table-column>
      <el-table-column label="详情" width="80"><template #default="{ row }"><el-button link @click="report = row">查看</el-button></template></el-table-column>
    </el-table>
  </section>

  <section v-if="report" class="panel mt24 market-report" data-testid="market-report">
    <div class="panel-heading"><div><p class="eyebrow">已保存的分析快照 · {{ new Date(report.data.created_at).toLocaleString('zh-CN') }}</p><h3>{{ report.data.query }} · 选品分析报告</h3>
      <p class="muted">{{ marketModeNames[report.data.mode] }} · {{ report.data.supply.title }} → {{ platformNames[report.data.retail.platform] }} · {{ report.data.retail.title }}</p></div>
      <el-button @click="download('/market/reports/' + report.id + '/export', '产品选品分析.csv')">导出报告</el-button>
    </div>
    <el-alert :title="recommendationNames[report.data.result.recommendation]" :type="report.data.result.blocked.length ? 'error' : 'info'" :closable="false" show-icon class="mb16"
      :description="report.data.mode === 'DEMO' ? '全部数据为模拟，不能据此采购。' : '结论针对当前输入和报价；不保证销量或实际利润。'" />
    <el-alert v-if="reportExpired" title="历史报价已超过24小时，请更新报价后决策" type="warning" :closable="false" class="mb16" />
    <el-alert v-if="reportConnectionChanged" title="此历史报告的平台连接已变更或停用，请重新查询核验" type="warning" :closable="false" class="mb16" />
    <div class="mini-metrics"><div><span>折算裸价差（尚未扣全部费用）</span><strong>¥{{ amount(report.data.result.sticker_spread) }}</strong></div>
      <div><span>预计正常贡献 / 压力贡献</span><strong>{{ report.data.result.normal ? '¥' + report.data.result.normal.display_contribution : '费用不完整' }} / {{ report.data.result.stress ? '¥' + report.data.result.stress.display_contribution : '—' }}</strong></div>
      <div><span>采购与投放初始现金</span><strong>¥{{ amount(report.data.result.initial_cash) }}</strong></div>
    </div>
    <p class="muted mb16">{{ report.data.result.cash_scope }}</p>
    <div v-if="report.data.supply.url || report.data.retail.url" class="market-links mb16">
      <a v-if="report.data.supply.url" :href="report.data.supply.url" target="_blank" rel="noopener noreferrer">查看1688报价来源 ↗</a>
      <a v-if="report.data.retail.url" :href="report.data.retail.url" target="_blank" rel="noopener noreferrer">查看{{ platformNames[report.data.retail.platform] }}报价来源 ↗</a>
    </div>
    <el-descriptions :column="2" border><el-descriptions-item label="折算每销售包装采购成本（含进货运费）">¥{{ amount(report.data.result.procurement_cost_per_sale_pack) }}</el-descriptions-item>
      <el-descriptions-item label="竞品到手价">¥{{ amount(report.data.result.retail_total) }}</el-descriptions-item>
      <el-descriptions-item label="盈亏平衡CAC">{{ report.data.result.normal ? '¥' + amount(report.data.result.normal.max_cac) : '费用不完整' }}</el-descriptions-item>
      <el-descriptions-item label="同款状态">{{ { CONFIRMED: '人工核验已确认', CONFLICT: '规格有冲突', UNVERIFIED: '待核验' }[report.data.result.match_state] }}</el-descriptions-item>
    </el-descriptions>
    <ul class="market-reasons"><li v-for="reason in [...report.data.result.blocked, ...report.data.result.pending, ...report.data.result.cautions]" :key="reason">{{ reason }}</li></ul>
    <div class="form-actions"><span class="muted">价格条件满足后，仍需完成样品、主备供货、资质及风险审核。</span>
      <el-button v-if="report.data.result.match_state === 'CONFIRMED'" type="primary" plain @click="candidateName = report.data.query; promoteOpen = true">保存为研究候选</el-button>
    </div>
  </section>

  <section class="panel mt24"><div class="panel-heading"><h3>查询与分析历史</h3><el-button size="small" @click="loadHistory">刷新历史</el-button></div>
    <el-tabs><el-tab-pane label="商品查询"><el-table :data="runs" empty-text="还没有查询记录">
      <el-table-column label="产品"><template #default="{ row }">{{ row.data.request.query }}</template></el-table-column>
      <el-table-column label="数据模式" width="120"><template #default="{ row }">{{ marketModeNames[row.data.request.mode as keyof typeof marketModeNames] }}</template></el-table-column>
      <el-table-column label="状态" width="120"><template #default="{ row }">{{ statusNames[row.data.status] }}</template></el-table-column>
      <el-table-column label="时间"><template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template></el-table-column>
      <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link @click="loadRun(row.id, true)">查看报价</el-button></template></el-table-column>
    </el-table></el-tab-pane>
      <el-tab-pane label="分析报告"><el-table :data="reports" empty-text="分析后会保留报告快照">
        <el-table-column label="产品"><template #default="{ row }">{{ row.data.query }}</template></el-table-column>
        <el-table-column label="比较平台"><template #default="{ row }">1688 → {{ platformNames[row.data.retail.platform as Platform] }}</template></el-table-column>
        <el-table-column label="结论"><template #default="{ row }">{{ recommendationNames[row.data.result.recommendation] }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link @click="report = row">查看报告</el-button></template></el-table-column>
      </el-table></el-tab-pane></el-tabs>
  </section>
  <MarketConnectionEditor v-model="connectionOpen" :connection="connection" @saved="value => connection = value" />
  <MarketManualQuotes v-model="manualOpen" :query="query" @saved="run => loadRun(run.id, true)" />
  <el-dialog v-model="promoteOpen" title="保存为研究候选" width="500px"><el-form label-position="top">
    <el-form-item label="候选名称"><el-input v-model="candidateName" /></el-form-item><el-form-item label="商品类目"><el-input v-model="category" /></el-form-item>
  </el-form><template #footer><el-button type="primary" @click="promote" :loading="analyzing">保存候选</el-button></template></el-dialog>
</template>
