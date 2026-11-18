<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ArrowRight, CircleCheck, Clock, Coin, Search } from '@element-plus/icons-vue'
import { request, refresh, amount, space } from '../api/client'
import { stateLabel, tagType } from '../catalog'
const router = useRouter(), data = ref<any>(null), error = ref(''), loading=ref(false)
const chartElement=ref<HTMLDivElement>(), dates=ref<string[]>([]), category=ref('')
echarts.use([BarChart,GridComponent,TooltipComponent,CanvasRenderer])
let chart:echarts.ECharts|undefined
async function load(){
  loading.value=true; error.value=''
  try{
    const q=new URLSearchParams(); if(dates.value?.[0])q.set('start',dates.value[0]);if(dates.value?.[1])q.set('end',dates.value[1]);if(category.value)q.set('category',category.value)
    data.value=await request('/dashboard?'+q);await nextTick();draw()
  }catch(e:any){error.value=e.message}finally{loading.value=false}
}
function draw(){
  if(!chartElement.value)return
  chart?.dispose();chart=echarts.init(chartElement.value)
  const states=data.value?.states||{}
  const keys=['DISCOVERED','RESEARCHING','READY_SAMPLE','SAMPLE_PASSED','READY_TEST','TESTING','SCALE']
  chart.setOption({grid:{left:80,right:36,top:10,bottom:15},tooltip:{trigger:'axis'},xAxis:{type:'value',minInterval:1,splitLine:{lineStyle:{color:'#edf0ed'}},axisLabel:{color:'#8b9997'}},yAxis:{type:'category',data:keys.map(stateLabel).reverse(),axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#657471'}},series:[{type:'bar',barWidth:14,itemStyle:{borderRadius:[0,4,4,0],color:'#25867a'},data:keys.map(k=>states[k]||0).reverse(),label:{show:true,position:'right',color:'#244b45'}}]})
}
function resize(){chart?.resize()}
onMounted(()=>{load();window.addEventListener('resize',resize)})
onUnmounted(()=>{chart?.dispose();window.removeEventListener('resize',resize)})
watch(refresh,load)
</script>
<template>
  <section class="page-heading"><div><p class="eyebrow">经营工作台</p><h1>今天的经营概况</h1><p class="muted">查看待核验事项，跟进测款与成熟订单贡献。</p></div><div class="heading-filter"><el-input v-model="category" placeholder="类目" clearable style="width:120px" @change="load"/><el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期（不含）" style="width:280px" @change="load"/></div></section>
  <el-alert v-if="error" :title="error" type="error" show-icon class="mb16"/><div v-if="loading&&!data" v-loading="true" class="loading-panel"/>
  <template v-if="data"><div class="metric-grid"><div class="metric-card"><div class="metric-top">候选商品<el-icon><Search/></el-icon></div><strong>{{data.candidate_count}}</strong><span>当前空间的研究机会</span></div><div class="metric-card"><div class="metric-top">待核验事项<el-icon><Clock/></el-icon></div><strong>{{data.todos.length}}</strong><span>报价、样品或审核需补充</span></div><div class="metric-card"><div class="metric-top">进行中测款预算<el-icon><Coin/></el-icon></div><strong><small>¥</small>{{amount(data.budget)}}</strong><span>已计划与进行中的广告预算</span></div><div class="metric-card accent"><div class="metric-top">成熟订单贡献<el-icon><CircleCheck/></el-icon></div><strong><small>¥</small>{{amount(data.mature_contribution)}}</strong><span>{{data.mature_orders}} 笔成熟且费用完整的订单</span></div></div>
  <div v-if="data.history" class="panel history-summary"><div><span class="eyebrow">UCI ONLINE RETAIL</span><h3>公开真实历史交易</h3><p class="muted">{{data.history.rows.toLocaleString()}} 行 · 数量合计 {{data.history.quantity.toLocaleString()}} · 交易金额 £{{amount(data.history.amount)}}</p></div><div><strong>{{data.history.unmatched_cancellations}}</strong><span>未匹配取消记录</span></div><el-button @click="router.push('/records/transactions')">查看历史交易 <el-icon><ArrowRight/></el-icon></el-button></div>
  <div class="dashboard-columns"><section class="panel"><div class="panel-heading"><div><h3>候选推进情况</h3><p class="muted">按研究阶段统计，审核条件由证据决定</p></div><el-button link @click="router.push('/records/candidates')">查看候选 <el-icon><ArrowRight/></el-icon></el-button></div><div ref="chartElement" class="state-chart"/></section><section class="panel"><div class="panel-heading"><div><h3>优先处理</h3><p class="muted">从证据缺失处继续研究</p></div><span class="count-badge">{{data.todos.length}}</span></div><div class="todo-list"><el-empty v-if="!data.todos.length" description="当前没有待核验候选" :image-size="70"/><button v-for="(item,index) in data.todos.slice(0,4)" :key="item.id" @click="router.push('/records/candidates?open='+item.id)" class="todo-item"><span class="todo-number">0{{index+1}}</span><div><strong>{{item.blocked[0]||item.missing[0]}}</strong><small>{{stateLabel(item.state)}} · {{item.missing.length}} 项待补充</small></div><el-tag :type="tagType(item.gate)" size="small" effect="plain">{{stateLabel(item.gate)}}</el-tag></button></div></section></div>
  <section class="panel"><div class="panel-heading"><div><h3>测款跟进</h3><p class="muted">成熟订单和待结算订单分别统计</p></div><el-button link @click="router.push('/records/experiments')">全部测款 <el-icon><ArrowRight/></el-icon></el-button></div><el-table :data="data.experiments" @row-click="router.push('/records/experiments')"><el-table-column label="测款假设" prop="data.hypothesis" min-width="260"/><el-table-column label="有效点击" prop="summary.clicks"/><el-table-column label="广告支出"><template #default="{row}">¥{{amount(row.summary.ad_spend)}}</template></el-table-column><el-table-column label="成熟贡献"><template #default="{row}"><span class="positive">¥{{amount(row.summary.mature_contribution)}}</span></template></el-table-column><el-table-column label="当前建议" width="130"><template #default="{row}"><el-tag :type="row.summary.recommendation==='PAUSE'?'danger':'info'" effect="plain">{{stateLabel(row.summary.recommendation)}}</el-tag></template></el-table-column></el-table></section>
  <section class="workflow-note"><div><strong>从机会到经营验证</strong><p>发现关键词，核验同款与供货，确认经济性，再通过真实订单复盘。</p></div><el-button @click="router.push('/data')">添加研究数据 <el-icon><ArrowRight/></el-icon></el-button></section>
  </template>
</template>
