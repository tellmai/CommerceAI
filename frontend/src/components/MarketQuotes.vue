<script setup lang="ts">
import { ref } from 'vue'
import { amount } from '../api/client'
import { platformNames, type MarketItem, type Platform } from '../api/market'

const props = defineProps<{ items: MarketItem[]; selected: string; supply?: boolean; loading?: boolean }>()
const emit = defineEmits<{ select: [item: MarketItem] }>()
const inspected = ref<MarketItem | null>(null)
function rowClass({ row }: { row: MarketItem }) { return row.id === props.selected ? 'market-selected' : '' }
</script>

<template>
  <el-table :data="items" v-loading="loading" row-key="id" empty-text="暂无商品。未开通接口时可使用上方免费检索入口。"
    :row-class-name="rowClass">
    <el-table-column label="商品" min-width="210">
      <template #default="{ row }"><strong>{{ row.title }}</strong><small class="muted" style="display:block;margin-top:5px">{{ row.seller || '商家信息待核验' }}</small></template>
    </el-table-column>
    <el-table-column v-if="!supply" label="平台" width="85"><template #default="{ row }">{{ platformNames[row.platform as Platform] }}</template></el-table-column>
    <el-table-column label="展示价" width="110"><template #default="{ row }">{{ row.price === null ? row.display_price || '价格未知' : '¥' + amount(row.price) }}<small class="muted" style="display:block">{{ row.currency }} · 待选规格</small></template></el-table-column>
    <el-table-column label="包装 / 运费" width="100"><template #default="{ row }"><span>{{ row.pack_count ? row.pack_count + '件' : '件数未知' }}</span><small class="muted" style="display:block">{{ row.shipping === null ? '运费未知' : '运费 ¥' + amount(row.shipping) }}</small></template></el-table-column>
    <el-table-column label="操作" width="135" fixed="right"><template #default="{ row }">
      <el-button link type="primary" @click="emit('select', row)">{{ row.id === selected ? '已选择' : '选择' }}</el-button>
      <el-button link @click="inspected = row">详情</el-button>
    </template></el-table-column>
  </el-table>
  <el-drawer :model-value="!!inspected" @close="inspected = null" title="商品报价与证据" size="min(720px, 92vw)">
    <template v-if="inspected">
      <h3>{{ inspected.title }}</h3><p class="muted mt24">{{ platformNames[inspected.platform] }} · {{ inspected.seller || '商家未知' }}</p>
      <a v-if="inspected.url" :href="inspected.url" target="_blank" rel="noopener noreferrer" class="positive">打开原商品核验</a>
      <el-descriptions :column="1" border class="mt24">
        <el-descriptions-item label="展示价">¥{{ amount(inspected.price) }} · 具体SKU见下表</el-descriptions-item>
        <el-descriptions-item label="起订量">{{ inspected.min_order ?? '未知' }}</el-descriptions-item>
        <el-descriptions-item label="包装件数">{{ inspected.pack_count ?? '未知' }}</el-descriptions-item>
        <el-descriptions-item label="采集时间">{{ new Date(inspected.observed_at).toLocaleString('zh-CN') }}</el-descriptions-item>
        <el-descriptions-item v-if="!inspected.evidence_note" label="源更新时间">{{ inspected.source_updated_at || '接口未提供，不能确认源数据更新时间' }}</el-descriptions-item>
        <el-descriptions-item label="详情状态">{{ inspected.evidence_note ? '人工报价' : inspected.detail_loaded ? '已读取详情' : '仅列表，详情待核验' }}</el-descriptions-item>
        <el-descriptions-item v-if="inspected.evidence_note" label="报价依据">{{ inspected.evidence_note }}</el-descriptions-item>
        <el-descriptions-item label="报价运费及地区">{{ inspected.shipping === null ? '运费未知' : '¥' + amount(inspected.shipping) }} · {{ inspected.shipping_region || '地区待核验' }}</el-descriptions-item>
        <el-descriptions-item label="原始规格属性"><pre class="json-preview">{{ JSON.stringify(inspected.attributes, null, 2) }}</pre></el-descriptions-item>
      </el-descriptions>
      <el-alert v-for="warning in inspected.warnings" :key="warning" :title="warning" type="warning" :closable="false" class="mt24" />
      <h4 class="mt24">具体 SKU</h4><el-table :data="inspected.variants"><el-table-column prop="label" label="规格" min-width="220" />
        <el-table-column label="价格"><template #default="{ row }">{{ row.price === null ? '待核验' : '¥' + amount(row.price) }}<small class="muted">{{ row.warning }}</small></template></el-table-column>
      </el-table>
      <h4 class="mt24">阶梯报价</h4><p class="muted">需核验报价单位及是否适用于所选SKU。多个SKU时不会自动套用商品级阶梯价。</p>
      <el-table :data="inspected.price_tiers"><el-table-column prop="min_quantity" label="起订数量" /><el-table-column prop="price" label="采购价" /></el-table>
    </template>
  </el-drawer>
</template>
