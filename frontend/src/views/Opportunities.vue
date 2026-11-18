<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { amount, openEditor, post, request, space, type Entity } from '../api/client'
import type { components } from '../api/schema'
import { procurementLabel, stateLabel } from '../catalog'

type Opportunity = components['schemas']['Opportunity']
type Page = components['schemas']['OpportunityPage']
type Baseline = components['schemas']['ForecastBaseline']
type Result = components['schemas']['ForecastResult']
type ForecastInput = components['schemas']['ForecastInput']
type Source = { candidate_id?: string; report_id?: string }
type Mode = 'STOCK' | 'DROPSHIP'
type TrafficReference = {experiment_id:string; name:string; channel:string; daily_clicks:number; conversion:string; start:string; end:string}
type Alternative = {price:string; low?:number; base?:number; high?:number}

const route = useRoute(), router = useRouter()
const data = ref<Page>(), source = ref<Source>({}), baseline = ref<Baseline>(), result = ref<Result>()
const detail = ref<Opportunity>(), formSection = ref<HTMLElement>()
const search = ref(''), page = ref(1), tableError = ref(''), calcError = ref('')
const loading = ref(false), baselineLoading = ref(false), calculating = ref(false)
const plans = ref<Entity[]>([]), plan = ref<Entity>(), planName = ref(''), planPage = ref(1), planTotal = ref(0)
const planError = ref(''), saveOpen = ref(false), saving = ref(false)
const trafficSources = ref<TrafficReference[]>([]), chosenTraffic = ref<TrafficReference>()
const alternatives = ref<Alternative[]>([])
const costs = [
  { key: 'c', label: '每单采购到手成本' },
  { key: 'f', label: '每单发货运费' },
  { key: 'b', label: '每单包材与服务费' },
  { key: 't', label: '每单平台费及变动税费' },
  { key: 'a', label: '每单广告费' },
  { key: 'l', label: '每次售后净损失' },
] as const
const costLabels: Record<string, string> = Object.fromEntries(costs.map(field => [field.key, field.label]))
costLabels.r = '售后损失概率'
const levels = {
  PRIORITY: { label: '优先研究', type: 'success' },
  TEST: { label: '继续观察', type: 'primary' },
  VERIFY: { label: '待补证据', type: 'warning' },
  BLOCKED: { label: '暂停推荐', type: 'danger' },
} as const
const actionNames = {
  RESEARCH: '补充研究依据', BUY_SAMPLE: '可买样核验', START_TEST: '可办理测款',
  TRACK_TEST: '查看测款进度', HOLD: '待复核后推进',
} as const
const pricing = {
  INCOMPLETE: '成本待补充',
  UNATTAINABLE: '当前售后损失概率与贡献目标下无法给出达标售价',
  COST_ONLY: '当前只有成本口径，售价建议为达标下限',
  ABOVE_REFERENCE: '达标售价高于已核验竞品中位价，先核对成本或差异化定价依据',
  WITHIN_REFERENCE: '竞品中位价可覆盖目标贡献，适合作为待测价格',
} as const
const scenarioNames = { LOW: '保守', BASE: '基准', HIGH: '乐观' } as const
const emptyCosts = { c: '', f: '', b: '', t: '', a: '', l: '' }
const form = reactive({
  ...emptyCosts, price: '', fixed_cost: '', r: undefined as number | undefined, margin: 20,
  days: 30, visits: undefined as number | undefined, low: undefined as number | undefined,
  base: undefined as number | undefined, high: undefined as number | undefined,
  mode: undefined as Mode | undefined, region: '全国', channel: '',
  denominator: 'VISITS' as 'VISITS' | 'CLICKS', fee: 0, refundAmount: undefined as number | undefined,
  stock: undefined as number | undefined, stockCash: '', capacity: undefined as number | undefined,
  settlement: undefined as number | undefined, adBudget: '', reserve: '',
})
const forecastRows = computed(() => {
  if (!result.value) return []
  const main = result.value.scenarios.map(row => ({...row, price: result.value!.effective_price}))
  return [...main, ...result.value.alternative_scenarios.flatMap(item => item.scenarios.map(row => ({...row, price:item.price})))]
})
const referenceNote = computed(() => {
  const count = result.value?.reference_count || 0
  return count ? count + ' 条有效同款报价' + (count < 5 ? ' · 样本较少' : '') : '暂无有效同款价'
})
const notation = (value?: string | null) => value == null ? '—' : '¥' + amount(value)
const percent = (value?: number | null) => value == null ? '—' : (value * 100).toFixed(1) + '%'

async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true; tableError.value = ''
  try {
    const query = new URLSearchParams({ search: search.value.trim(), page: String(page.value), page_size: '12' })
    data.value = await request<Page>('/opportunities?' + query)
  } catch (e: any) { tableError.value = e.message }
  finally { loading.value = false }
}
async function selectSource(value: Source) {
  calcError.value = ''; result.value = undefined
  plan.value = undefined; chosenTraffic.value = undefined; trafficSources.value = []
  if (!value.candidate_id && !value.report_id) {
    source.value = {}; baseline.value = undefined
    Object.assign(form, emptyCosts, {r:undefined, price:'', fee:0, mode:undefined, stock:undefined, stockCash:'', region:'全国'})
    return
  }
  const query = new URLSearchParams()
  if (value.candidate_id) query.set('candidate_id', value.candidate_id)
  else if (value.report_id) query.set('report_id', value.report_id)
  baselineLoading.value = true
  try {
    const selected = await request<Baseline>('/opportunities/baseline?' + query)
    source.value = value; baseline.value = selected
    for (const item of costs) form[item.key] = selected.inputs[item.key] || ''
    form.r = selected.inputs.r == null ? undefined : Number(selected.inputs.r) * 100
    form.price = selected.price || ''
    form.fee = Number(selected.inputs.fee_rate ?? 0) * 100
    form.mode = selected.procurement_mode
    form.region = selected.region
    form.stock = selected.stock_units ?? undefined
    form.stockCash = selected.stock_batch_cash || ''
    if (value.candidate_id) await loadTraffic(value.candidate_id)
    await nextTick()
    formSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (e: any) { calcError.value = e.message }
  finally { baselineLoading.value = false }
}
async function loadTraffic(id: string) {
  try { trafficSources.value = await request<TrafficReference[]>('/forecasts/traffic?candidate_id=' + encodeURIComponent(id)) }
  catch (e: any) { calcError.value = e.message }
}
function applyTraffic(value: TrafficReference) {
  chosenTraffic.value = value
  form.denominator = 'CLICKS'
  form.channel = value.channel; form.visits = value.daily_clicks; form.base = Number(value.conversion) * 100
}
async function loadPlans() {
  planError.value = ''
  try {
    const page = await request<{items:Entity[]; total:number}>(`/forecasts?page=${planPage.value}&page_size=12`)
    plans.value = page.items; planTotal.value = page.total
  } catch (e: any) { planError.value = e.message }
}
async function inspectPlan(id: string) {
  try {
    const item = await request<Entity>('/forecasts/' + id)
    const input = item.data.inputs as ForecastInput, traffic = input.traffic
    baseline.value = undefined
    source.value = {candidate_id:input.candidate_id || undefined, report_id:input.report_id || undefined}
    chosenTraffic.value = input.traffic?.history_experiment_id ? {...item.data.anchor.traffic,
      experiment_id: input.traffic.history_experiment_id} : undefined
    for (const cost of costs) form[cost.key] = input[cost.key] ?? ''
    Object.assign(form, {
      price: input.price ?? '', fixed_cost: input.fixed_cost ?? '',
      margin: Number(input.target_margin) * 100, r: input.r == null ? undefined : Number(input.r) * 100,
      fee: Number(input.fee_rate ?? 0) * 100,
      mode: input.procurement_mode || undefined, region: input.region || '全国',
      channel: input.channel || '', stock: input.stock_units ?? undefined, stockCash: input.stock_batch_cash || '',
      capacity: input.dropship_capacity_per_day ?? undefined, settlement: input.settlement_days ?? undefined,
      adBudget: input.advertising_budget || '', reserve: input.refund_reserve || '',
      refundAmount: input.refund_amount_rate == null ? undefined : Number(input.refund_amount_rate) * 100,
      denominator: traffic?.denominator || 'VISITS', days: traffic?.days ?? 30, visits: traffic?.daily_visits,
      low: traffic ? Number(traffic.low) * 100 : undefined, base: traffic ? Number(traffic.base) * 100 : undefined,
      high: traffic ? Number(traffic.high) * 100 : undefined,
    })
    alternatives.value = (input.alternative_prices || []).map(choice => ({
      price: choice.price, low: Number(choice.low) * 100, base: Number(choice.base) * 100, high: Number(choice.high) * 100,
    }))
    plan.value = item; planName.value = item.data.name
    result.value = item.data.result
    trafficSources.value = []
    if (item.data.candidate_id) await loadTraffic(item.data.candidate_id)
    await nextTick()
    formSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (e: any) { planError.value = e.message }
}
function startPlan(item: Entity) {
  const input = item.data.inputs
  const start = (Math.floor((Date.now() + 8 * 3600e3) / 864e5) + 1) * 864e5 - 8 * 3600e3
  openEditor('experiments', {
    candidate_id: item.data.candidate_id, forecast_id: item.id,
    start_at: new Date(start).toISOString(), end_at: new Date(start + input.traffic.days * 864e5).toISOString(),
    budget: input.advertising_budget, hypothesis: item.data.name,
  })
}
function planUsable(item: Entity) {
  return !!(item.data.candidate_id && item.data.inputs.channel && item.data.result.scenarios?.length && !item.data.result.missing.length)
}
function example() {
  if (!baseline.value) Object.assign(form, { price: '49.90', c: '15', f: '4', b: '1', t: '1', a: '3', r: 8, l: '8' })
  Object.assign(form, { visits: 200, low: 2, base: 4, high: 6, days: 30, fixed_cost: '200' })
  calculate()
}
function forecastInputs(): ForecastInput {
  const rates = [form.low, form.base, form.high]
  const hasTraffic = form.visits != null || rates.some(rate => rate != null)
  if (hasTraffic && (form.visits == null || rates.some(rate => rate == null) || !form.days)) {
    throw new Error('计算收入请填写预测天数、日均流量和三档转化率。')
  }
  if (hasTraffic && !(form.low! <= form.base! && form.base! <= form.high!)) {
    throw new Error('转化率须按保守、基准、乐观从低到高填写。')
  }
  if (form.margin == null) {
    throw new Error('请填写目标贡献率。')
  }
  const history = chosenTraffic.value
  const fromHistory = history && form.denominator === 'CLICKS' && form.channel === history.channel
    && form.visits === history.daily_clicks && form.base === Number(history.conversion) * 100
  return {
    ...source.value,
    ...Object.fromEntries(costs.map(field => [field.key, form[field.key].trim() || null])),
    price: form.price.trim() || null, target_margin: form.margin / 100, fee_rate: form.fee / 100,
    region: form.region.trim() || null, channel: form.channel.trim() || null, procurement_mode: form.mode || null,
    stock_units: form.stock ?? null, stock_batch_cash: form.stockCash.trim() || null,
    dropship_capacity_per_day: form.capacity ?? null, settlement_days: form.settlement ?? null,
    advertising_budget: form.adBudget.trim() || null, refund_reserve: form.reserve.trim() || null,
    refund_amount_rate: form.refundAmount == null ? null : form.refundAmount / 100,
    fixed_cost: form.fixed_cost.trim() || null, r: form.r == null ? null : form.r / 100,
    traffic: hasTraffic ? {days: form.days, daily_visits: form.visits!, low: form.low! / 100,
      base: fromHistory ? Number(history.conversion) : form.base! / 100, high: form.high! / 100,
      denominator: form.denominator, history_experiment_id: fromHistory ? history.experiment_id : null} : null,
    alternative_prices: alternatives.value.map(v => {
      if (!v.price.trim() || v.low == null || v.base == null || v.high == null) throw new Error('请填写备选售价及对应的三档转化率。')
      return {price:v.price.trim(), low:v.low / 100, base:v.base / 100, high:v.high / 100}
    }),
  }
}
async function calculate() {
  calcError.value = ''; result.value = undefined
  calculating.value = true
  try {
    result.value = await post<Result>('/opportunities/forecast', forecastInputs())
  } catch (e: any) {
    const fields = e.details?.errors?.map((problem: {loc: string[]}) => {
      const key = problem.loc.at(-1) || ''
      return costLabels[key] || ({ price: '计划成交单价', target_margin: '目标贡献率', fixed_cost: '预测期固定成本' } as Record<string, string>)[key] || key
    })
    calcError.value = e.message + (fields?.length ? '：' + fields.join('、') : '')
  } finally { calculating.value = false }
}
async function savePlan() {
  saving.value = true; planError.value = ''
  try {
    const item = await post<Entity>('/forecasts', {name:planName.value.trim(), inputs:forecastInputs(), parent_forecast_id:plan.value?.id || null})
    saveOpen.value = false; plan.value = item; result.value = item.data.result
    planPage.value = 1
    await loadPlans()
  } catch (e: any) { planError.value = e.message }
  finally { saving.value = false }
}
onMounted(() => {
  load()
  if (space.value === 'public') return
  loadPlans()
  if (typeof route.query.forecast === 'string') inspectPlan(route.query.forecast)
  else if (typeof route.query.report === 'string') selectSource({ report_id: route.query.report })
  else if (typeof route.query.candidate === 'string') selectSource({ candidate_id: route.query.candidate })
})
</script>

<template>
  <section class="page-heading">
    <div><h1>爆款推荐</h1><p class="muted">从已登记的商品中筛出值得测款的机会，并估算定价、成本和收入。</p></div>
    <el-button @click="$router.push('/discover')">找货与比价</el-button>
  </section>
  <el-alert v-if="space === 'demo'" class="mb16" title="模拟演示数据。实际选品请切换真实经营空间。" type="info" :closable="false" />
  <el-alert v-if="space === 'public'" class="mb16" title="请切换到真实经营或模拟演示空间查看推荐与预测" type="info" :closable="false" />
  <template v-else>
    <section class="panel">
      <div class="panel-heading">
        <div><h3>潜在爆款候选</h3><p class="muted">研究优先级和可执行动作分别依据需求、报价和审核状态给出。</p></div>
        <el-input v-model="search" placeholder="搜索商品、类目或关键词" aria-label="搜索爆款候选" clearable
          class="opportunity-search" maxlength="100" @keyup.enter="load(true)" @clear="load(true)" />
      </div>
      <el-alert v-if="tableError" :title="tableError" type="error" class="mb16" :closable="false" />
      <el-table v-loading="loading" :data="data?.items || []" row-key="id"
        empty-text="暂无候选。可从「找货与比价」保存研究候选，或在「选品中心」登记商品。">
        <el-table-column label="商品" min-width="200">
          <template #default="{ row }"><strong>{{ row.name }}</strong><p class="muted">{{ row.category }} · {{ procurementLabel(row.procurement_mode) }} · {{ row.keyword || '暂无关联关键词' }}</p></template>
        </el-table-column>
        <el-table-column label="优先级 / 下一步" width="155">
          <template #default="{ row }"><el-tag :type="levels[(row as Opportunity).level].type" effect="plain">{{ levels[(row as Opportunity).level].label }}</el-tag><p class="muted">{{ actionNames[(row as Opportunity).next_action] }}</p></template>
        </el-table-column>
        <el-table-column label="评分 / 覆盖率" min-width="145">
          <template #default="{ row }">
            <span v-if="row.score != null">{{ row.grade || '' }} {{ row.score.toFixed(1) }}</span>
            <span v-else-if="row.score_low != null">{{ row.score_low.toFixed(1) }}–{{ row.score_high.toFixed(1) }}</span>
            <span v-else>{{ row.score_stale ? '评分过期' : '暂无有效评分' }}</span>
            <p v-if="row.coverage != null" class="muted">{{ percent(row.coverage) }}</p>
          </template>
        </el-table-column>
        <el-table-column label="需求 / 增长" width="135">
          <template #default="{ row }">
            {{ row.demand_factor?.toFixed(1) ?? '—' }} / {{ row.growth_factor?.toFixed(1) ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="165">
          <template #default="{ row }">
            <el-button link @click="detail = row">查看依据</el-button>
            <el-button link type="primary" :disabled="baselineLoading" @click="selectSource({ candidate_id: row.id })">定价预测</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="data && data.total > 12" v-model:current-page="page" :page-size="12" :total="data.total" class="mt24"
        layout="prev, pager, next" @current-change="load()" />
    </section>

    <section ref="formSection" class="panel mt24" v-loading="baselineLoading">
      <div class="panel-heading">
        <div><h3>定价与收入预测</h3>
          <p class="muted">{{ plan ? plan.data.name + ' · 已保存版本 ' + plan.data.plan_version : baseline ? baseline.name + ' · ' + (baseline.source === 'CANDIDATE' ? '候选利润情景' : '四平台比价报告') : '选择候选带入已有成本，或手动录入商品假设。' }}</p>
        </div>
        <div class="forecast-buttons">
          <el-button v-if="baseline || plan" @click="selectSource({})">清除关联</el-button>
          <el-button v-if="space === 'demo'" @click="example" :disabled="calculating">载入演练假设</el-button>
        </div>
      </div>
      <el-alert v-for="notice in baseline?.notices || []" :key="notice" :title="notice" class="mb16" type="warning" :closable="false" />
      <el-alert v-if="calcError" :title="calcError" class="mb16" type="error" :closable="false" />
      <el-form label-position="top" @submit.prevent="calculate">
        <div class="forecast-inputs">
          <fieldset>
            <legend>每单售价与成本</legend>
            <div class="record-form">
              <el-form-item label="计划成交单价"><el-input v-model="form.price" placeholder="留空采用可用建议价"><template #prefix>¥</template></el-input></el-form-item>
              <el-form-item label="目标贡献率（%）"><el-input-number v-model="form.margin" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="采购方式"><el-select v-model="form.mode" :disabled="!!(source.candidate_id || source.report_id)" clearable placeholder="选择采购方式">
                <el-option label="小批备货" value="STOCK" /><el-option label="一件代发" value="DROPSHIP" />
              </el-select></el-form-item>
              <el-form-item label="适用报价地区"><el-input v-model="form.region" /></el-form-item>
              <el-form-item v-for="item in costs" :key="item.key" :label="item.label">
                <el-input v-model="form[item.key]" placeholder="未知留空"><template #prefix>¥</template></el-input>
              </el-form-item>
              <el-form-item label="售后损失概率（%）"><el-input-number v-model="form.r" :min="0" :max="100" :precision="2" :controls="false" placeholder="未知留空" /></el-form-item>
              <el-form-item label="按成交价计的平台费率（%）"><el-input-number v-model="form.fee" :min="0" :max="100" :precision="4" :controls="false" /></el-form-item>
              <el-form-item label="预计退款额占支付收入（%）"><el-input-number v-model="form.refundAmount" :min="0" :max="100" :precision="2" :controls="false" placeholder="未知留空" /></el-form-item>
            </div>
            <p class="muted">成交单价按顾客实付口径填写。费用确认无支出才填 0。</p>
          </fieldset>
          <fieldset>
            <legend>预测期与流量假设</legend>
            <div class="record-form">
              <el-form-item label="预测天数"><el-input-number v-model="form.days" :min="1" :max="365" :precision="0" :controls="false" /></el-form-item>
              <el-form-item label="日均有效访问量"><el-input-number v-model="form.visits" :min="0" :max="10000000" :precision="0" :controls="false" placeholder="输入访问量假设" /></el-form-item>
              <el-form-item label="测款渠道"><el-input v-model="form.channel" placeholder="例如搜索，与漏斗渠道保持一致" /></el-form-item>
              <el-form-item label="流量口径"><el-select v-model="form.denominator"><el-option label="有效访问" value="VISITS" /><el-option label="有效点击" value="CLICKS" /></el-select></el-form-item>
              <el-form-item label="保守转化率（%）"><el-input-number v-model="form.low" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="基准转化率（%）"><el-input-number v-model="form.base" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="乐观转化率（%）"><el-input-number v-model="form.high" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="预测期固定成本"><el-input v-model="form.fixed_cost" placeholder="选填，用于测算扣除固定成本后结果"><template #prefix>¥</template></el-input></el-form-item>
            </div>
            <el-dropdown v-if="trafficSources.length" class="mb16" @command="applyTraffic">
              <el-button>带入已核验历史基准</el-button>
              <template #dropdown><el-dropdown-menu>
                <el-dropdown-item v-for="item in trafficSources" :key="item.experiment_id + item.channel" :command="item">{{ item.channel }} · {{ item.start }} 至 {{ item.end }} · {{ percent(Number(item.conversion)) }}</el-dropdown-item>
              </el-dropdown-menu></template>
            </el-dropdown>
            <p v-if="chosenTraffic" class="muted">参考 {{ chosenTraffic.channel }} {{ chosenTraffic.start }} 至 {{ chosenTraffic.end }}，请填写保守和乐观转化率。</p>
            <p class="muted">三档收入按相同售价和成本、不同支付转化率测算。没有访问量与转化率时可先只算定价。</p>
          </fieldset>
          <fieldset v-if="form.mode">
            <legend>{{ procurementLabel(form.mode) }}约束与资金</legend>
            <div class="record-form">
              <template v-if="form.mode === 'STOCK'">
                <el-form-item label="可售库存套数"><el-input-number v-model="form.stock" :min="0" :max="1000000000000" :precision="0" :controls="false" placeholder="未知留空" /></el-form-item>
                <el-form-item label="首批货款与进货运费"><el-input v-model="form.stockCash" placeholder="未知留空" /></el-form-item>
              </template>
              <template v-else>
                <el-form-item label="核验后的日代发上限"><el-input-number v-model="form.capacity" :min="0" :max="10000000" :precision="0" :controls="false" placeholder="未知留空" /></el-form-item>
                <el-form-item label="回款滞后天数"><el-input-number v-model="form.settlement" :min="0" :max="365" :precision="0" :controls="false" placeholder="未知留空" /></el-form-item>
              </template>
              <el-form-item label="投放现金预算"><el-input v-model="form.adBudget" placeholder="未知留空" /></el-form-item>
              <el-form-item label="退款准备金"><el-input v-model="form.reserve" placeholder="未知留空" /></el-form-item>
            </div>
          </fieldset>
          <fieldset>
            <legend>备选售价</legend>
            <div v-for="(option, index) in alternatives" :key="index" class="record-form">
              <el-form-item :label="'备选售价 ' + (index + 1)"><el-input v-model="option.price" /></el-form-item>
              <el-form-item v-for="(name, key) in {low:'保守', base:'基准', high:'乐观'}" :key="key" :label="name + '转化率（%）'">
                <el-input-number v-model="option[key]" :min="0" :max="100" :precision="2" :controls="false" />
              </el-form-item>
              <el-button link @click="alternatives.splice(index,1)">移除备选售价</el-button>
            </div>
            <el-button v-if="alternatives.length < 2" @click="alternatives.push({price:'',low:form.low,base:form.base,high:form.high})">添加备选售价</el-button>
          </fieldset>
        </div>
        <div class="form-actions">
          <el-button @click="planError = ''; planName = plan?.data.name || baseline?.name || ''; saveOpen = true">{{ plan ? '另存方案版本' : '保存预测方案' }}</el-button>
          <el-button type="primary" native-type="submit" :loading="calculating">计算定价与预测</el-button>
        </div>
      </el-form>
    </section>

    <section v-if="result" class="panel mt24 forecast-result" data-testid="forecast-result">
      <div class="panel-heading"><div><h3>定价建议</h3><p class="muted">{{ pricing[result.pricing_status] }}</p></div></div>
      <el-alert v-if="result.missing.length" :title="'待补成本：' + result.missing.map(key => costLabels[key] || key).join('、')" type="warning" class="mb16" :closable="false" />
      <el-alert v-for="notice in result.notices" :key="notice" :title="notice" class="mb16" type="warning" :closable="false" />
      <div class="mini-metrics">
        <div><span>建议待测售价</span><strong>{{ notation(result.suggested_price) }}</strong></div>
        <div><span>满足贡献目标的最低售价</span><strong>{{ notation(result.minimum_target_price) }}</strong></div>
        <div><span>目标采购到手成本上限</span><strong>{{ notation(result.max_purchase_cost) }}</strong></div>
      </div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="同款到手价中位数">{{ notation(result.reference_price) }} · {{ referenceNote }}</el-descriptions-item>
        <el-descriptions-item label="实际采用的测算售价">{{ notation(result.effective_price) }}</el-descriptions-item>
        <el-descriptions-item label="每单期望变动成本">{{ notation(result.unit_variable_cost) }}</el-descriptions-item>
        <el-descriptions-item label="每单期望贡献 / 贡献率">{{ notation(result.unit_contribution) }} / {{ percent(result.unit_contribution_rate) }}</el-descriptions-item>
        <el-descriptions-item label="单笔盈亏平衡价">{{ notation(result.break_even_price) }}</el-descriptions-item>
        <el-descriptions-item label="每支付订单广告成本上限">{{ notation(result.max_cac) }}</el-descriptions-item>
        <el-descriptions-item label="达到目标贡献率的广告上限">{{ notation(result.max_target_cac) }}</el-descriptions-item>
        <el-descriptions-item v-if="result.reference_price_gap != null" label="达标价与竞品差额 / 每单需降成本">
          {{ notation(result.reference_price_gap) }} / {{ notation(result.reference_cost_reduction) }}
        </el-descriptions-item>
      </el-descriptions>
      <template v-if="forecastRows.length">
        <div class="panel-heading mt24"><div><h3>三档收入与贡献预测</h3>
          <p class="muted">需求由流量与转化率决定。填报库存或代发上限时，收入按可履约订单估算。</p></div></div>
        <el-table :data="forecastRows">
          <el-table-column label="售价" width="100"><template #default="{ row }">{{ notation(row.price) }}</template></el-table-column>
          <el-table-column label="情景" width="78"><template #default="{ row }">{{ scenarioNames[row.name as keyof typeof scenarioNames] }}</template></el-table-column>
          <el-table-column label="支付转化率" width="110"><template #default="{ row }">{{ percent(row.conversion) }}</template></el-table-column>
          <el-table-column label="支付订单 / 需求" width="135"><template #default="{ row }">{{ row.orders.toLocaleString() }} / {{ row.demand_orders.toLocaleString() }}<small v-if="row.shortfall" class="muted" style="display:block">缺口 {{ row.shortfall }}</small></template></el-table-column>
          <el-table-column label="支付收入" min-width="115"><template #default="{ row }">{{ notation(row.paid_revenue) }}</template></el-table-column>
          <el-table-column label="预计净收入" min-width="125"><template #default="{ row }">{{ notation(row.net_revenue) }}</template></el-table-column>
          <el-table-column label="期望变动成本" min-width="125"><template #default="{ row }">{{ notation(row.variable_cost) }}</template></el-table-column>
          <el-table-column label="总贡献" min-width="115"><template #default="{ row }">{{ notation(row.contribution) }}</template></el-table-column>
          <el-table-column label="扣固定成本后" min-width="130"><template #default="{ row }">{{ notation(row.after_fixed_cost) }}</template></el-table-column>
          <el-table-column v-if="form.mode === 'STOCK'" label="余货套数" min-width="100"><template #default="{ row }">{{ row.remaining_stock ?? '—' }}</template></el-table-column>
          <el-table-column v-if="form.mode" label="资金需求" min-width="110"><template #default="{ row }">{{ notation(row.cash_required) }}</template></el-table-column>
        </el-table>
        <p v-if="result.break_even_orders != null" class="muted mt24">覆盖预测期固定成本所需支付订单 {{ result.break_even_orders.toLocaleString() }} 单。</p>
      </template>
      <p v-else class="muted mt24">填写访问量、转化率和可用售价后可查看收入预测。</p>
    </section>
    <section class="panel mt24">
      <div class="panel-heading"><h3>已保存的预测方案</h3></div>
      <el-alert v-if="planError && !saveOpen" :title="planError" type="error" :closable="false" />
      <el-table :data="plans" empty-text="保存预测后可查看历史或采用到测款">
        <el-table-column label="方案" min-width="180"><template #default="{row}">{{row.data.name}} · 版本 {{row.data.plan_version}}</template></el-table-column>
        <el-table-column label="采购 / 渠道" min-width="160"><template #default="{row}">{{procurementLabel(row.data.inputs.procurement_mode)}} · {{row.data.inputs.channel || '未选渠道'}}</template></el-table-column>
        <el-table-column label="测算售价" min-width="100"><template #default="{row}">{{notation(row.data.result.effective_price)}}</template></el-table-column>
        <el-table-column label="操作" width="185"><template #default="{row}">
          <el-button link type="primary" @click="inspectPlan(row.id)">查看方案</el-button>
          <el-button v-if="planUsable(row)" link @click="startPlan(row)">采用到测款</el-button>
        </template></el-table-column>
      </el-table>
      <el-pagination v-if="planTotal > 12" v-model:current-page="planPage" :page-size="12" :total="planTotal" class="mt24" layout="prev, pager, next" @current-change="loadPlans" />
    </section>
  </template>

  <el-dialog v-model="saveOpen" title="保存预测方案" width="min(480px, 90vw)">
    <el-alert v-if="planError" :title="planError" type="error" :closable="false" class="mb16"/>
    <el-form label-position="top" @submit.prevent="savePlan">
      <el-form-item label="方案名称"><el-input v-model="planName" maxlength="100" /></el-form-item>
    </el-form>
    <template #footer><el-button type="primary" :loading="saving" :disabled="!planName.trim()" @click="savePlan">保存方案</el-button></template>
  </el-dialog>
  <el-drawer :model-value="!!detail" @close="detail = undefined" title="候选推荐依据" size="min(620px, 94vw)">
    <template v-if="detail">
      <h2>{{ detail.name }}</h2><p class="muted">{{ detail.category }} · {{ stateLabel(detail.state) }}</p>
      <h3 class="mt24">{{ levels[detail.level].label }}</h3>
      <p class="muted">{{ actionNames[detail.next_action] }}</p>
      <ul v-if="detail.blocked.length || detail.missing.length" class="market-reasons">
        <li v-for="reason in [...detail.blocked, ...detail.missing]" :key="reason">{{ reason }}</li>
      </ul>
      <p v-else class="muted mt24">当前必要证据齐备。请结合测款结果决定采购量。</p>
      <el-button type="primary" plain @click="router.push('/records/candidates?open=' + detail.id)">打开候选研究</el-button>
      <el-button v-if="detail.missing.includes('需求证据不足')" link @click="router.push('/records/keywords')">补充关键词数据</el-button>
    </template>
  </el-drawer>
</template>
