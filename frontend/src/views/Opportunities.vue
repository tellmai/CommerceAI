<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { amount, post, request, space } from '../api/client'
import type { components } from '../api/schema'
import { stateLabel } from '../catalog'

type Opportunity = components['schemas']['Opportunity']
type Page = components['schemas']['OpportunityPage']
type Baseline = components['schemas']['ForecastBaseline']
type Result = components['schemas']['ForecastResult']
type Source = { candidate_id?: string; report_id?: string }

const route = useRoute()
const data = ref<Page>(), source = ref<Source>({}), baseline = ref<Baseline>(), result = ref<Result>()
const detail = ref<Opportunity>(), formSection = ref<HTMLElement>()
const search = ref(''), page = ref(1), tableError = ref(''), calcError = ref('')
const loading = ref(false), baselineLoading = ref(false), calculating = ref(false)
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
  PRIORITY: { label: '优先验证', type: 'success' },
  TEST: { label: '可考虑小批测款', type: 'primary' },
  VERIFY: { label: '待核验', type: 'warning' },
  BLOCKED: { label: '暂停推荐', type: 'danger' },
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
  if (!value.candidate_id && !value.report_id) {
    source.value = {}; baseline.value = undefined
    Object.assign(form, emptyCosts, { r: undefined, price: '' })
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
    await nextTick()
    formSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (e: any) { calcError.value = e.message }
  finally { baselineLoading.value = false }
}
function example() {
  if (!baseline.value) Object.assign(form, { price: '49.90', c: '15', f: '4', b: '1', t: '1', a: '3', r: 8, l: '8' })
  Object.assign(form, { visits: 200, low: 2, base: 4, high: 6, days: 30, fixed_cost: '200' })
  calculate()
}
async function calculate() {
  calcError.value = ''; result.value = undefined
  const rates = [form.low, form.base, form.high]
  const hasTraffic = form.visits != null || rates.some(rate => rate != null)
  if (hasTraffic && (form.visits == null || rates.some(rate => rate == null) || !form.days)) {
    calcError.value = '计算收入请填写预测天数、日均有效访问量和三档转化率。'
    return
  }
  if (hasTraffic && !(form.low! <= form.base! && form.base! <= form.high!)) {
    calcError.value = '转化率须按保守、基准、乐观从低到高填写。'
    return
  }
  if (form.margin == null) {
    calcError.value = '请填写目标贡献率。'
    return
  }
  calculating.value = true
  try {
    result.value = await post<Result>('/opportunities/forecast', {
      ...source.value,
      ...Object.fromEntries(costs.map(field => [field.key, form[field.key].trim() || null])),
      price: form.price.trim() || null, target_margin: form.margin / 100,
      fixed_cost: form.fixed_cost.trim() || null, r: form.r == null ? null : form.r / 100,
      traffic: hasTraffic ? {days: form.days, daily_visits: form.visits, low: form.low! / 100, base: form.base! / 100, high: form.high! / 100} : null,
    })
  } catch (e: any) {
    const fields = e.details?.errors?.map((problem: {loc: string[]}) => {
      const key = problem.loc.at(-1) || ''
      return costLabels[key] || ({ price: '计划成交单价', target_margin: '目标贡献率', fixed_cost: '预测期固定成本' } as Record<string, string>)[key] || key
    })
    calcError.value = e.message + (fields?.length ? '：' + fields.join('、') : '')
  } finally { calculating.value = false }
}
onMounted(() => {
  load()
  if (space.value === 'public') return
  if (typeof route.query.report === 'string') selectSource({ report_id: route.query.report })
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
        <div><h3>潜在爆款候选</h3><p class="muted">按有效评分、需求证据和审核结果排序。优先验证代表值得小批测试。</p></div>
        <el-input v-model="search" placeholder="搜索商品、类目或关键词" aria-label="搜索爆款候选" clearable
          class="opportunity-search" maxlength="100" @keyup.enter="load(true)" @clear="load(true)" />
      </div>
      <el-alert v-if="tableError" :title="tableError" type="error" class="mb16" :closable="false" />
      <el-table v-loading="loading" :data="data?.items || []" row-key="id"
        empty-text="暂无候选。可从「找货与比价」保存研究候选，或在「选品中心」登记商品。">
        <el-table-column label="商品" min-width="200">
          <template #default="{ row }"><strong>{{ row.name }}</strong><p class="muted">{{ row.category }} · {{ row.keyword || '暂无关联关键词' }}</p></template>
        </el-table-column>
        <el-table-column label="建议" width="155">
          <template #default="{ row }"><el-tag :type="levels[(row as Opportunity).level].type" effect="plain">{{ levels[(row as Opportunity).level].label }}</el-tag></template>
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
          <p class="muted">{{ baseline ? baseline.name + ' · ' + (baseline.source === 'CANDIDATE' ? '候选利润情景' : '四平台比价报告') : '选择候选带入已有成本，或手动录入商品假设。' }}</p>
        </div>
        <div class="forecast-buttons">
          <el-button v-if="baseline" @click="selectSource({})">清除关联</el-button>
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
              <el-form-item v-for="item in costs" :key="item.key" :label="item.label">
                <el-input v-model="form[item.key]" placeholder="未知留空"><template #prefix>¥</template></el-input>
              </el-form-item>
              <el-form-item label="售后损失概率（%）"><el-input-number v-model="form.r" :min="0" :max="100" :precision="2" :controls="false" placeholder="未知留空" /></el-form-item>
            </div>
            <p class="muted">成交单价按顾客实付口径填写。费用确认无支出才填 0。</p>
          </fieldset>
          <fieldset>
            <legend>预测期与流量假设</legend>
            <div class="record-form">
              <el-form-item label="预测天数"><el-input-number v-model="form.days" :min="1" :max="365" :precision="0" :controls="false" /></el-form-item>
              <el-form-item label="日均有效访问量"><el-input-number v-model="form.visits" :min="0" :max="10000000" :precision="0" :controls="false" placeholder="输入访问量假设" /></el-form-item>
              <el-form-item label="保守转化率（%）"><el-input-number v-model="form.low" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="基准转化率（%）"><el-input-number v-model="form.base" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="乐观转化率（%）"><el-input-number v-model="form.high" :min="0" :max="100" :precision="2" :controls="false" /></el-form-item>
              <el-form-item label="预测期固定成本"><el-input v-model="form.fixed_cost" placeholder="选填，用于测算扣除固定成本后结果"><template #prefix>¥</template></el-input></el-form-item>
            </div>
            <p class="muted">三档收入按相同售价和成本、不同支付转化率测算。没有访问量与转化率时可先只算定价。</p>
          </fieldset>
        </div>
        <div class="form-actions"><el-button type="primary" native-type="submit" :loading="calculating">计算定价与预测</el-button></div>
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
      </el-descriptions>
      <template v-if="result.scenarios.length">
        <div class="panel-heading mt24"><div><h3>三档收入与贡献预测</h3>
          <p class="muted">订单量按预测期有效访问量 × 支付转化率向下取整。留存收入按售后损失概率折算。</p></div></div>
        <el-table :data="result.scenarios" row-key="name">
          <el-table-column label="情景" width="85"><template #default="{ row }">{{ scenarioNames[row.name as keyof typeof scenarioNames] }}</template></el-table-column>
          <el-table-column label="支付转化率" width="110"><template #default="{ row }">{{ percent(row.conversion) }}</template></el-table-column>
          <el-table-column label="支付订单" width="100"><template #default="{ row }">{{ row.orders.toLocaleString() }}</template></el-table-column>
          <el-table-column label="支付收入" min-width="115"><template #default="{ row }">{{ notation(row.paid_revenue) }}</template></el-table-column>
          <el-table-column label="期望留存收入" min-width="125"><template #default="{ row }">{{ notation(row.retained_revenue) }}</template></el-table-column>
          <el-table-column label="期望变动成本" min-width="125"><template #default="{ row }">{{ notation(row.variable_cost) }}</template></el-table-column>
          <el-table-column label="总贡献" min-width="115"><template #default="{ row }">{{ notation(row.contribution) }}</template></el-table-column>
          <el-table-column label="扣固定成本后" min-width="130"><template #default="{ row }">{{ notation(row.after_fixed_cost) }}</template></el-table-column>
        </el-table>
        <p v-if="result.break_even_orders != null" class="muted mt24">覆盖预测期固定成本所需支付订单 {{ result.break_even_orders.toLocaleString() }} 单。</p>
      </template>
      <p v-else class="muted mt24">填写访问量、转化率和可用售价后可查看收入预测。</p>
    </section>
  </template>

  <el-drawer :model-value="!!detail" @close="detail = undefined" title="候选推荐依据" size="min(620px, 94vw)">
    <template v-if="detail">
      <h2>{{ detail.name }}</h2><p class="muted">{{ detail.category }} · {{ stateLabel(detail.state) }}</p>
      <h3 class="mt24">{{ levels[detail.level].label }}</h3>
      <ul v-if="detail.blocked.length || detail.missing.length" class="market-reasons">
        <li v-for="reason in [...detail.blocked, ...detail.missing]" :key="reason">{{ reason }}</li>
      </ul>
      <p v-else class="muted mt24">当前必要证据齐备。请结合测款结果决定采购量。</p>
      <el-button type="primary" plain @click="$router.push('/records/candidates?open=' + detail.id)">打开候选研究</el-button>
    </template>
  </el-drawer>
</template>
