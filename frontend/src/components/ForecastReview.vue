<script setup lang="ts">
import { amount } from '../api/client'

type Metric = {
  key: string
  unit: 'money' | 'ratio' | 'count'
  forecast: string | number | null
  actual: string | number | null
  difference: string | number | null
}
defineProps<{ comparison: {
  forecast_id: string; name: string; version: number; start: string; end: string; channel: string
  state: string; missing: string[]; coverage: number | null; comparable_traffic: boolean; metrics: Metric[]
} }>()
const names: Record<string, string> = {
  traffic: '有效点击', conversion: '支付转化率', orders: '支付订单', avg_price: '均单实付价',
  paid_revenue: '支付收入', net_revenue: '净收入', refunds: '退款金额', advertising: '广告支出',
  purchase_unit: '计划采购到手 / 实际均单采购净支出', contribution: '贡献利润',
}
function display(row: Metric, value: Metric['actual'], change = false) {
  if (value == null) return '—'
  if (row.unit === 'ratio') return (Number(value)*100).toFixed(2) + (change ? ' 个百分点' : '%')
  if (row.unit === 'count') return Number(value).toLocaleString()
  return '¥' + amount(value)
}
</script>

<template>
  <section class="mt24" data-testid="forecast-review">
    <div class="panel-heading">
      <div><h3>预测与实际对照</h3><p class="muted">{{comparison.name}} · 版本 {{comparison.version}} · {{comparison.channel}} · {{comparison.start}} 至 {{comparison.end}}（不含）</p></div>
      <router-link class="positive" :to="{path:'/opportunities',query:{forecast:comparison.forecast_id}}">查看预测方案</router-link>
    </div>
    <el-alert
      :title="comparison.state==='RECONCILED' ? '观察期已结束，渠道与订单账本已对齐' : '仍在观察或对账，偏差待确认'"
      :description="comparison.missing.join(' · ')" :type="comparison.state==='RECONCILED'?'success':'warning'" :closable="false" class="mb16" />
    <p class="muted mb16">成熟贡献覆盖率 {{comparison.coverage == null ? '—' : (comparison.coverage*100).toFixed(1)+'%'}}。{{comparison.comparable_traffic ? '' : '预测按有效访问量填报，账本按点击记录，流量与转化偏差暂不比较。'}}</p>
    <el-table :data="comparison.metrics">
      <el-table-column label="指标" min-width="190"><template #default="{row}">{{row.key==='traffic' && !comparison.comparable_traffic ? '有效访问' : names[row.key] || row.key}}</template></el-table-column>
      <el-table-column label="基准预测" min-width="112"><template #default="{row}">{{display(row,row.forecast)}}</template></el-table-column>
      <el-table-column label="账本实际" min-width="112"><template #default="{row}">{{display(row,row.actual)}}</template></el-table-column>
      <el-table-column label="实际 − 预测" min-width="120"><template #default="{row}">{{display(row,row.difference,true)}}</template></el-table-column>
    </el-table>
  </section>
</template>
