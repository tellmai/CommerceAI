<script setup lang="ts">
import { ref, watch } from 'vue'
import { post, put } from '../api/client'
import { platformNames, type ManualQuote, type MarketRun, type Platform } from '../api/market'

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ query: string; editing?: MarketRun | null }>()
const emit = defineEmits<{ saved: [run: MarketRun] }>()
const newQuote = (platform: Platform): ManualQuote => ({
  platform, title: '', url: '', price: '', pack_count: null, material: '', size: '', brand: '',
  model: '', min_order: null, shipping: null, shipping_region: '', observed_at: null, evidence_note: '',
  seller: '', variant_key: '', price_kind: 'LIST', eligibility_verified: false, conditions: '', price_tiers: [],
})
const retailPlatforms: Platform[] = ['taobao', 'jd', 'pinduoduo']
const quotes = ref([newQuote('1688'), newQuote('taobao')])
const saving = ref(false), error = ref('')
const fields = [
  { key: 'title', label: '商品标题', required: true },
  { key: 'url', label: '原商品链接', required: true },
  { key: 'price', label: '每包装报价（元）', required: true },
  { key: 'seller', label: '店铺或供应商' },
  { key: 'variant_key', label: '已选 SKU 标识' },
  { key: 'material', label: '材质' },
  { key: 'size', label: '尺寸或型号规格' },
  { key: 'brand', label: '品牌' },
  { key: 'model', label: '型号' },
] as const

watch(open, value => {
  if (value) {
    quotes.value = props.editing?.data.items.map(item => ({
      ...newQuote(item.platform), title: item.title, url: item.url, price: item.price || '',
      pack_count: item.pack_count, min_order: item.min_order, shipping: item.shipping,
      shipping_region: item.shipping_region, observed_at: item.observed_at, seller: item.seller,
      variant_key: item.variant_key || '', price_kind: item.price_kind || 'LIST',
      eligibility_verified: item.eligibility_verified || false, conditions: item.conditions || '',
      price_tiers: item.price_tiers.map(tier => ({...tier})), evidence_note: item.evidence_note || '',
      material: item.attributes.material || '', size: item.attributes.size || '',
      brand: item.attributes.brand || '', model: item.attributes.model || '',
    })) || [newQuote('1688'), newQuote('taobao')]
    error.value = ''
  }
})
function addQuote(platform: Platform) {
  quotes.value.push(newQuote(platform))
}
async function save() {
  error.value = ''
  if (quotes.value.some(quote => !quote.title.trim() || !quote.url.trim() ||
    !quote.price.trim() || quote.evidence_note.trim().length < 5)) {
    error.value = '每条报价需填写标题、链接、每包装报价和至少5字的依据。'
    return
  }
  saving.value = true
  try {
    const input = {
      query: (props.editing?.data.request.query || props.query).trim(),
      quotes: quotes.value.map(quote => ({
        ...quote,
        shipping: quote.shipping?.trim() || null,
        observed_at: quote.observed_at ? new Date(quote.observed_at).toISOString() : null,
      })),
    }
    const run: MarketRun = props.editing
      ? await put(`/market/runs/${props.editing.id}/quotes`, {...input, expected_version: props.editing.version})
      : await post<MarketRun>('/market/runs/manual', input)
    open.value = false
    emit('saved', run)
  } catch (e: any) { error.value = e.message }
  finally { saving.value = false }
}
</script>

<template>
  <el-dialog v-model="open" :title="editing ? '更新平台报价' : '录入平台报价'" width="min(850px, 94vw)">
    <p class="muted mb16">同一商品可录入多个货源和多个卖家。更新价格时请同时核对采集时间，旧报价保存在版本历史中。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16" />
    <section v-for="(quote, index) in quotes" :key="index" class="market-manual-quote" :aria-label="platformNames[quote.platform] + '报价'">
      <div class="panel-heading">
        <h3>{{ platformNames[quote.platform] }} · 第 {{ index + 1 }} 条报价</h3>
        <el-button v-if="quotes.length > 2" link @click="quotes.splice(index, 1)">移除此报价</el-button>
      </div>
      <el-form label-position="top" class="record-form">
        <el-form-item v-if="quote.platform !== '1688'" label="零售平台">
          <el-select v-model="quote.platform" style="width:100%">
            <el-option v-for="platform in retailPlatforms" :key="platform" :value="platform" :label="platformNames[platform]" />
          </el-select>
        </el-form-item>
        <el-form-item v-for="field in fields" :key="field.key" :label="field.label"
          :required="'required' in field" :class="{ full: field.key === 'url' }">
          <el-input v-model="quote[field.key]" :placeholder="field.key === 'url' ? '从平台复制链接' : undefined" />
        </el-form-item>
        <el-form-item label="每包装件数"><el-input-number v-model="quote.pack_count" :min="1" :max="100000" :controls="false" placeholder="未知" /></el-form-item>
        <el-form-item v-if="quote.platform === '1688'" label="最少起订包装数"><el-input-number v-model="quote.min_order" :min="1" :max="1000000" :controls="false" placeholder="未知" /></el-form-item>
        <el-form-item label="页面运费（元）"><el-input v-model="quote.shipping" placeholder="未知请留空" /></el-form-item>
        <el-form-item label="报价地区"><el-input v-model="quote.shipping_region" placeholder="例如浙江杭州" /></el-form-item>
        <el-form-item label="采集时间"><el-date-picker v-model="quote.observed_at" type="datetime" placeholder="留空记为录入时刻" /></el-form-item>
        <el-form-item v-if="editing" label="重新采集"><el-button @click="quote.observed_at = null">按本次录入记时间</el-button></el-form-item>
        <template v-if="quote.platform !== '1688'">
          <el-form-item label="零售价口径"><el-select v-model="quote.price_kind">
            <el-option label="挂牌价" value="LIST" /><el-option label="优惠价" value="ELIGIBLE" />
          </el-select></el-form-item>
          <el-form-item v-if="quote.price_kind === 'ELIGIBLE'" label="优惠适用条件">
            <el-input v-model="quote.conditions" placeholder="例如需领券或达到满减额" />
            <el-checkbox v-model="quote.eligibility_verified">已核验优惠资格</el-checkbox>
          </el-form-item>
        </template>
        <el-form-item v-else label="采购阶梯价" class="full">
          <div v-for="(tier, tierIndex) in quote.price_tiers" :key="tierIndex" class="quote-tier">
            <el-input-number v-model="tier.min_quantity" :min="1" :controls="false" aria-label="阶梯起订包装数" />
            <el-input v-model="tier.price" aria-label="阶梯每包装价格" placeholder="每包装价格（元）" />
            <el-button link @click="quote.price_tiers?.splice(tierIndex, 1)">移除此阶梯</el-button>
          </div>
          <el-button v-if="(quote.price_tiers?.length || 0) < 20" @click="quote.price_tiers?.push({min_quantity: 10, price: ''})">添加阶梯价</el-button>
        </el-form-item>
        <el-form-item label="报价依据" required class="full"><el-input v-model="quote.evidence_note" type="textarea" :rows="2" maxlength="1500" /></el-form-item>
      </el-form>
    </section>
    <div v-if="quotes.length < 30" class="forecast-buttons">
      <el-button @click="addQuote('1688')">添加1688货源</el-button>
      <el-button @click="addQuote('taobao')">添加零售报价</el-button>
    </div>
    <template #footer><el-button type="primary" :loading="saving" @click="save">保存报价并开始比较</el-button></template>
  </el-dialog>
</template>
