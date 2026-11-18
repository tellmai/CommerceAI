<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Download, Refresh, ArrowRight, Search } from '@element-plus/icons-vue'
import { request, post, refresh, openEditor, referenceKinds, entityName, pretty, amount, download, type Entity } from '../api/client'
import { resources, labels, stateLabel, tagType, transitions } from '../catalog'
import RefSelect from '../components/RefSelect.vue'
const props=defineProps<{resourceKind?:string,embedded?:boolean}>()
const route=useRoute(),router=useRouter()
const kind=computed(()=>props.resourceKind||String(route.params.kind||'candidates'))
const definition=computed(()=>resources[kind.value]||resources.candidates)
const groups=[['products','skus','listings','matches','prices','signals'],['suppliers','offers','samples'],['candidates','assessments'],['experiments','funnels','orders'],['sources','evidence','transactions']]
const tabs=computed(()=>props.embedded?[]:groups.find(g=>g.includes(kind.value))||[])
const items=ref<Entity[]>([]), total=ref(0),page=ref(1),search=ref(''), stateFilter=ref(''), category=ref('')
const busy=ref(false),error=ref(''), selected=ref<Entity|null>(null), research=ref<any>(null), summary=ref<any>(null),history=ref<any[]>([]), related=ref<Record<string,Entity[]>>({}),labelCache=ref<Record<string,string>>({})
const drawerTab=ref('overview'), transitionOpen=ref(false),target=ref(''),reason=ref(''), matchOpen=ref(false),matchTarget=ref('MATCHED'),evidence=ref<string|null>(null),actionBusy=ref(false),actionError=ref('')
const relatedTabs=computed(()=>kind.value==='candidates'?['profits','assessments','risks']:kind.value==='experiments'?['funnels','orders']:kind.value==='skus'?['matches','offers']:[])
async function load(){
  busy.value=true;error.value=''
  try{
    const query=new URLSearchParams({page:String(page.value),page_size:'20',search:search.value})
    if(stateFilter.value)query.set('state',stateFilter.value);if(category.value)query.set('category',category.value)
    const response=await request<{items:Entity[],total:number}>(`/${kind.value}?${query}`);items.value=response.items;total.value=response.total
    await resolveLabels(items.value)
  }catch(e:any){error.value=e.message}finally{busy.value=false}
}
async function resolveLabels(records:Entity[]){
  const pending:Promise<any>[]=[]
  for(const row of records)for(const [field,resource] of Object.entries(referenceKinds)){
    const id=row.data[field];if(id&&!labelCache.value[id])pending.push(request<Entity>(`/${resource}/${id}`).then(entity=>labelCache.value[id]=entityName(entity)).catch(()=>labelCache.value[id]='记录已不可用'))
  }
  await Promise.all(pending)
}
function display(key:string,value:any){
  if(value===null||value===undefined)return '待核验'
  if(referenceKinds[key])return labelCache.value[value]||'正在读取…'
  if(['conversion','mall_click_share','r','head_share'].includes(key))return (Number(value)*100).toFixed(1)+'%'
  if(['p','c','a','price','shipping','unit_cost','budget','ad_spend','revenue','customer_refund','unit_price'].includes(key))return amount(value)
  if(typeof value==='boolean')return value?'是':'否'
  if(typeof value==='string'&&/^\d{4}-\d\d-\d\dT/.test(value))return new Date(value).toLocaleString('zh-CN',{hour12:false})
  if(Array.isArray(value))return value.map(x=>resources[x]?.title||x).join('、')
  if(value&&typeof value==='object')return Object.entries(value).map(([k,v])=>`${labels[k]||k}: ${v}`).join(' · ')
  return stateLabel(value)
}
async function inspect(row:Entity){
  selected.value=row; research.value=null;summary.value=null;drawerTab.value='overview';related.value={};history.value=[];actionError.value=''
  try{
    if(kind.value==='candidates')research.value=await request(`/candidates/${row.id}/research`)
    if(kind.value==='experiments')summary.value=await request(`/experiments/${row.id}/summary`)
    if(kind.value==='skus')research.value={comparisons:await request(`/skus/${row.id}/comparison`)}
    if(kind.value==='keywords')research.value=await request(`/keywords/${row.id}/analysis`)
    history.value=(await request(`/history/${row.id}`)).items
    for(const name of relatedTabs.value){const r=await request(`/${name}?parent_id=${row.id}&page_size=100`);related.value[name]=r.items;await resolveLabels(r.items)}
    await resolveLabels([row])
  }catch(e:any){actionError.value=e.message}
}
async function score(){
  if(!selected.value)return
  actionBusy.value=true
  try{
    const job=await post(`/candidates/${selected.value.id}/score`,{})
    ElMessage.success('评分任务已提交')
    for(let i=0;i<30;i++){
      await new Promise(resolve=>setTimeout(resolve,500))
      const result=await request(`/jobs/${job.id}`)
      if(result.status==='FAILED')throw new Error(result.error)
      if(result.status==='SUCCEEDED'){await inspect(selected.value);refresh.value++;return}
    }
    ElMessage.info('任务仍在处理，可在数据中心查看进度')
  }catch(e:any){actionError.value=e.message}finally{actionBusy.value=false}
}
async function changeState(){
  if(!selected.value)return
  actionBusy.value=true;actionError.value=''
  try{await post(`/candidates/${selected.value.id}/transition`,{target_state:target.value,expected_version:selected.value.version,reason:reason.value,evidence_ids:[]});transitionOpen.value=false;selected.value=null;refresh.value++;ElMessage.success('阶段已更新，审核记录已保存')}
  catch(e:any){actionError.value=e.message+(e.details?.missing?.length?'：'+e.details.missing.join('、'):'')+(e.details?.blocked?.length?'：'+e.details.blocked.join('、'):'')}
  finally{actionBusy.value=false}
}
async function reviewMatch(){
  if(!selected.value)return
  actionBusy.value=true
  try{const row=await post(`/matches/${selected.value.id}/review`,{state:matchTarget.value,evidence_id:evidence.value,reason:reason.value,expected_version:selected.value.version});matchOpen.value=false;await inspect(row);refresh.value++;ElMessage.success('同款审核已保存')}
  catch(e:any){actionError.value=e.message+(e.details?.conflicts?.length?'：'+e.details.conflicts.join('、'):'')}
  finally{actionBusy.value=false}
}
async function allocate(){if(!selected.value)return;try{await post(`/experiments/${selected.value.id}/allocate-ads`);ElMessage.success('广告费已分摊并保留版本');await inspect(selected.value);refresh.value++}catch(e:any){actionError.value=e.message}}
async function exportRows(format:string){try{await download(`/exports/${kind.value}?format=${format}&search=${encodeURIComponent(search.value)}&state=${stateFilter.value}&category=${encodeURIComponent(category.value)}`,`${kind.value}.${format}`)}catch(e:any){ElMessage.error(e.message)}}
function startTransition(){target.value=transitions[selected.value?.data.state]?.[0]||'';reason.value='';actionError.value='';transitionOpen.value=true}
watch([kind,refresh],()=>{if(!props.embedded)page.value=1;load()},{immediate:true})
watch(refresh,async()=>{
  if(!selected.value)return
  const tab=drawerTab.value,id=selected.value.id
  try{
    const current=await request<Entity>(`/${kind.value}/${id}`)
    if(selected.value?.id!==id)return
    await inspect(current);drawerTab.value=tab
  }catch(e:any){actionError.value=e.message}
})
watch(()=>route.query.open,async id=>{if(id&&!props.embedded){try{await inspect(await request<Entity>(`/${kind.value}/${id}`))}catch(e:any){error.value=e.message}}},{immediate:true})
</script>
<template>
  <section v-if="!embedded" class="page-heading"><div><p class="eyebrow">{{kind==='candidates'?'选品中心':'研究资料'}}</p><h1>{{definition.title}}</h1><p class="muted">{{definition.subtitle}}</p></div><el-button type="primary" @click="openEditor(kind)"><el-icon><Plus/></el-icon>新增{{definition.title}}</el-button></section>
  <div v-if="tabs.length" class="section-tabs"><router-link v-for="tab in tabs" :key="tab" :to="'/records/'+tab" :class="{active:kind===tab}">{{resources[tab].title}}</router-link></div>
  <section class="panel resource-panel"><div class="table-toolbar"><div class="table-filters"><el-input v-model="search" placeholder="搜索名称、规格或备注" clearable style="width:260px" @keyup.enter="page=1;load()" @clear="load"><template #prefix><el-icon><Search/></el-icon></template></el-input><el-select v-if="kind==='candidates'" v-model="stateFilter" clearable placeholder="全部阶段" style="width:135px" @change="page=1;load()"><el-option v-for="(options,key) in transitions" :key="key" :label="stateLabel(key)" :value="key"/></el-select><el-input v-if="['keywords','products'].includes(kind)" v-model="category" placeholder="筛选类目" clearable style="width:140px" @change="load"/></div><div class="table-tools"><el-button v-if="embedded" type="primary" plain @click="openEditor(kind)">新增</el-button><el-button :icon="Refresh" @click="load" aria-label="刷新列表"/><el-dropdown @command="exportRows"><el-button :icon="Download">导出</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="csv">CSV</el-dropdown-item><el-dropdown-item command="xlsx">Excel</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16"/>
  <el-table :data="items" v-loading="busy" @row-dblclick="inspect" row-key="id" :empty-text="error?'加载失败，请点击刷新':'当前空间暂无记录，添加数据后开始研究'" style="width:100%">
    <el-table-column v-for="(column,index) in definition.columns" :key="column" :label="labels[column]||column" :min-width="index===0?210:['reason','note','attributes'].includes(column)?200:125" show-overflow-tooltip>
      <template #default="{row}"><el-tag v-if="['state','status','result','quality_flag','cancellation_status'].includes(column)" :type="tagType(row.data[column])" effect="plain" size="small">{{stateLabel(row.data[column])}}</el-tag><span v-else :class="{'cell-primary':index===0,'muted':row.data[column]===null}">{{display(column,row.data[column])}}</span></template>
    </el-table-column>
    <el-table-column v-if="kind==='candidates'" label="上次评分" width="100"><template #default="{row}"><strong>{{row.data._summary?.score===null?'—':Number(row.data._summary?.score).toFixed(1)}}</strong><small class="muted" style="display:block">{{row.data._summary?.score_stale?'已更新 · 待重算':row.data._summary?.coverage===null?'未评分':'覆盖 '+Math.round(row.data._summary.coverage*100)+'%'}}</small></template></el-table-column>
    <el-table-column v-if="kind==='candidates'" label="正常贡献" width="110"><template #default="{row}"><span class="positive">¥{{amount(row.data._summary?.contribution)}}</span></template></el-table-column>
    <el-table-column v-if="kind==='candidates'" label="当前审核" width="110"><template #default="{row}"><el-tag :type="tagType(row.data._summary?.gate)" effect="plain" size="small">{{stateLabel(row.data._summary?.gate)}}</el-tag></template></el-table-column>
    <el-table-column label="操作" fixed="right" width="150"><template #default="{row}"><el-button link type="primary" @click="inspect(row)">{{kind==='candidates'?'研究详情':'查看'}}</el-button><el-button v-if="!definition.immutable" link @click="openEditor(kind,{},row)">修改</el-button></template></el-table-column>
  </el-table><div class="table-bottom"><span>{{total.toLocaleString()}} 条记录 · 数据空间独立</span><el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="prev,pager,next" @current-change="load"/></div></section>
  <el-drawer :model-value="!!selected" @close="selected=null" size="min(1000px, 92vw)" :title="selected?entityName(selected):''" destroy-on-close>
    <template v-if="selected"><div class="drawer-toolbar"><el-tag effect="plain">版本 {{selected.version}}</el-tag><el-tag v-if="selected.data.state" :type="tagType(selected.data.state)">{{stateLabel(selected.data.state)}}</el-tag><div class="spacer"/><el-button v-if="kind==='candidates'" @click="score" :loading="actionBusy">重新评分</el-button><el-button v-if="kind==='candidates'" type="primary" @click="startTransition">推进阶段</el-button><el-button v-if="kind==='matches'" type="primary" @click="matchOpen=true;evidence=selected.data.evidence_id;reason='';actionError=''">审核同款</el-button><el-button v-if="kind==='experiments'" @click="allocate">分摊广告费</el-button></div>
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="mb16"/>
    <el-tabs v-model="drawerTab"><el-tab-pane label="研究概览" name="overview">
      <template v-if="research?.requirements"><el-alert :title="stateLabel(research.requirements.gate)" :description="[...research.requirements.blocked,...research.requirements.missing].join('；')||'当前有效证据已满足测款审核条件'" :type="research.requirements.gate==='PASS'?'success':research.requirements.gate==='BLOCKED'?'error':'warning'" :closable="false" show-icon class="mb16"/>
      <el-alert v-if="research.score_stale" title="历史评分待核验" description="研究输入、策略或证据已更新或过期，请重新评分。历史结果仍可按原输入复算。" type="warning" :closable="false" show-icon class="mb16"/><div v-if="research.score" class="score-panel"><div><span class="eyebrow">综合研究评分</span><strong>{{research.score.data.score===null?'—':research.score.data.score.toFixed(1)}}<small>/100</small></strong><p>{{research.score.data.grade||'待核验'}} · 覆盖率 {{Math.round(research.score.data.coverage*100)}}%</p></div><div class="factor-list"><div v-for="(value,key) in research.score.data.factors" :key="key"><span>{{labels[key]||key}}</span><el-progress :percentage="value??0" :show-text="false" :color="value===null?'#dbe3de':'#29887c'"/><strong>{{value===null?'未知':Number(value).toFixed(0)}}</strong></div></div></div><p v-else class="muted">尚无评分，补充研究数据后可提交评分任务。</p></template>
      <div v-if="research?.strategies" class="strategy-grid"><div v-for="(value,key) in research.strategies" :key="key"><strong>{{stateLabel(key)}}</strong><el-tag :type="tagType(value)" effect="plain">{{stateLabel(value)}}</el-tag></div></div>
      <div v-if="summary" class="experiment-result"><div class="mini-metrics"><div><span>广告支出 / 预算剩余</span><strong>¥{{amount(summary.ad_spend)}} / ¥{{amount(summary.remaining_budget)}}</strong></div><div><span>成熟贡献 / 订单</span><strong>¥{{amount(summary.mature_contribution)}} / {{summary.mature_orders}}</strong></div><div><span>未成熟 / 费用不完整</span><strong>{{summary.provisional_orders}} / {{summary.incomplete_orders}}</strong></div></div><el-alert :title="stateLabel(summary.recommendation)" :description="summary.pause_reasons.join('；')||'继续记录有效点击、成熟订单及实际费用，再进行人工复盘。'" :closable="false" type="info" class="mb16"/></div>
      <el-descriptions :column="2" border><el-descriptions-item v-for="column in Object.keys(selected.data).filter(k=>!k.startsWith('_')&&!['config','input_snapshot','records','result','score_run_id'].includes(k))" :key="column" :label="labels[column]||column">{{display(column,selected.data[column])}}</el-descriptions-item></el-descriptions>
      <div v-if="research?.comparisons" class="mt24"><h3>同规格零售价格带</h3><div class="mini-metrics"><div><span>有效报价</span><strong>{{research.comparisons.statistics.count}} 条</strong></div><div><span>零售中位价</span><strong>¥{{amount(research.comparisons.statistics.median)}}</strong></div><div><span>样本状态</span><strong>{{research.comparisons.statistics.sparse?'样本不足':'可比较'}}</strong></div></div><el-table :data="research.comparisons.included"><el-table-column prop="platform" label="平台"/><el-table-column prop="title" label="商品"/><el-table-column prop="total" label="到手价"/></el-table><h4 v-if="research.comparisons.excluded.length" class="mt24">排除的报价</h4><el-table :data="research.comparisons.excluded"><el-table-column prop="title" label="商品"/><el-table-column prop="reason" label="排除原因"/></el-table></div>
    </el-tab-pane>
    <el-tab-pane v-for="name in relatedTabs" :key="name" :label="resources[name].title" :name="name"><div class="table-toolbar"><span>{{related[name]?.length||0}} 条相关记录</span><el-button type="primary" plain @click="openEditor(name,{[kind==='experiments'?'experiment_id':kind==='skus'?'sku_id':'candidate_id']:selected.id})">新增{{resources[name].title}}</el-button></div><el-table :data="related[name]||[]"><el-table-column v-for="column in resources[name].columns.filter(k=>!['candidate_id','experiment_id'].includes(k))" :key="column" :label="labels[column]||column" min-width="130" show-overflow-tooltip><template #default="{row}">{{display(column,row.data[column])}}</template></el-table-column><el-table-column label="操作" width="70"><template #default="{row}"><el-button v-if="!resources[name].immutable" link @click="openEditor(name,{},row)">修改</el-button></template></el-table-column></el-table></el-tab-pane>
    <el-tab-pane label="历史版本" name="history"><el-timeline><el-timeline-item v-for="record in history" :key="record.version" :timestamp="new Date(record.created_at).toLocaleString('zh-CN')"><strong>版本 {{record.version}} · {{record.actor}}</strong><details><summary>查看历史快照</summary><pre class="json-preview">{{JSON.stringify(record.data,null,2)}}</pre></details></el-timeline-item></el-timeline></el-tab-pane></el-tabs></template>
  </el-drawer>
  <el-dialog v-model="transitionOpen" title="人工审核并推进阶段" width="500px"><el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="mb16"/><el-form label-position="top"><el-form-item label="目标阶段"><el-select v-model="target" style="width:100%"><el-option v-for="value in transitions[selected?.data.state]||[]" :key="value" :label="stateLabel(value)" :value="value"/></el-select></el-form-item><el-form-item label="审核理由" required><el-input v-model="reason" type="textarea" :rows="3"/></el-form-item></el-form><template #footer><el-button @click="transitionOpen=false">取消</el-button><el-button type="primary" :loading="actionBusy" @click="changeState">保存审核与阶段</el-button></template></el-dialog>
  <el-dialog v-model="matchOpen" title="同款人工审核" width="520px"><el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="mb16"/><el-form label-position="top"><el-form-item label="审核结论"><el-select v-model="matchTarget"><el-option v-for="option in ['MATCHED','REJECTED','ALTERNATIVE']" :key="option" :label="stateLabel(option)" :value="option"/></el-select></el-form-item><el-form-item label="核验证据"><RefSelect v-model="evidence" kind="evidence"/></el-form-item><el-form-item label="审核理由"><el-input v-model="reason" type="textarea"/></el-form-item></el-form><template #footer><el-button type="primary" :loading="actionBusy" @click="reviewMatch">保存审核</el-button></template></el-dialog>
</template>
