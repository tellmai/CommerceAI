<script setup lang="ts">
import { ref, watch } from 'vue'
import { post } from '../api/client'
import { platformNames, type ManualQuote, type MarketRun, type Platform } from '../api/market'

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ query: string }>()
const emit = defineEmits<{ saved: [run: MarketRun] }>()
const newQuote = (platform: Platform): ManualQuote => ({
  platform, title: '', url: '', price: '', pack_count: null, material: '', size: '', brand: '',
  model: '', min_order: null, shipping: null, shipping_region: '', observed_at: null, evidence_note: '',
})
const retailPlatforms: Platform[] = ['taobao', 'jd', 'pinduoduo']
const quotes = ref([newQuote('1688'), newQuote('taobao')])
const saving = ref(false), error = ref('')
const fields = [
  { key: 'title', label: '商品标题', required: true },
  { key: 'url', label: '原商品链接', required: true },
  { key: 'price', label: '每包装报价（元）', required: true },
  { key: 'material', label: '材质' },
  { key: 'size', label: '尺寸或型号规格' },
  { key: 'brand', label: '品牌' },
  { key: 'model', label: '型号' },
] as const

watch(open, value => {
  if (value) {
    quotes.value = [newQuote('1688'), newQuote('taobao')]
    error.value = ''
  }
})
function addRetail() {
  const next = retailPlatforms.find(platform => !quotes.value.some(quote => quote.platform === platform))
  if (next) quotes.value.push(newQuote(next))
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
    const run = await post<MarketRun>('/market/runs/manual', {
      query: props.query.trim(),
      quotes: quotes.value.map(quote => ({
        ...quote,
        shipping: quote.shipping?.trim() || null,
        observed_at: quote.observed_at ? new Date(quote.observed_at).toISOString() : null,
      })),
    })
    open.value = false
    emit('saved', run)
  } catch (e: any) { error.value = e.message }
  finally { saving.value = false }
}
</script>

<template>
  <el-dialog v-model="open" title="录入平台报价" width="min(850px, 94vw)">
    <p class="muted mb16">分别填写所选规格的人民币报价。请在依据中注明阶梯数量、促销条件或凭据位置。未知运费留空，确认免运费才填 0。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16" />
    <section v-for="(quote, index) in quotes" :key="index" class="market-manual-quote" :aria-label="platformNames[quote.platform] + '报价'">
      <div class="panel-heading">
        <h3>{{ index === 0 ? '1688 货源' : '零售报价 ' + index }}</h3>
        <el-button v-if="index > 1" link @click="quotes.splice(index, 1)">移除此报价</el-button>
      </div>
      <el-form label-position="top" class="record-form">
        <el-form-item v-if="index !== 0" label="零售平台">
          <el-select v-model="quote.platform" style="width:100%">
            <el-option v-for="platform in retailPlatforms" :key="platform" :value="platform" :label="platformNames[platform]"
              :disabled="quotes.some((other, position) => position !== index && other.platform === platform)" />
          </el-select>
        </el-form-item>
        <el-form-item v-for="field in fields" :key="field.key" :label="field.label"
          :required="'required' in field" :class="{ full: field.key === 'url' }">
          <el-input v-model="quote[field.key]" :placeholder="field.key === 'url' ? '从平台复制链接' : undefined" />
        </el-form-item>
        <el-form-item label="每包装件数"><el-input-number v-model="quote.pack_count" :min="1" :max="100000" :controls="false" placeholder="未知" /></el-form-item>
        <el-form-item v-if="index === 0" label="最少起订包装数"><el-input-number v-model="quote.min_order" :min="1" :max="1000000" :controls="false" placeholder="未知" /></el-form-item>
        <el-form-item label="页面运费（元）"><el-input v-model="quote.shipping" placeholder="未知请留空" /></el-form-item>
        <el-form-item label="报价地区"><el-input v-model="quote.shipping_region" placeholder="例如浙江杭州" /></el-form-item>
        <el-form-item label="采集时间"><el-date-picker v-model="quote.observed_at" type="datetime" placeholder="留空记为录入时刻" /></el-form-item>
        <el-form-item label="报价依据" required class="full"><el-input v-model="quote.evidence_note" type="textarea" :rows="2" maxlength="1500" /></el-form-item>
      </el-form>
    </section>
    <el-button v-if="quotes.length < 4" @click="addRetail">添加另一零售平台</el-button>
    <template #footer><el-button type="primary" :loading="saving" @click="save">保存报价并开始比较</el-button></template>
  </el-dialog>
</template>
