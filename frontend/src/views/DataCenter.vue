<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Document, Download } from '@element-plus/icons-vue'
import { request, post, download, meta, space, refresh, openEditor, referenceKinds, type Entity, entityName } from '../api/client'
import { labels, resources, stateLabel, tagType } from '../catalog'
import RefSelect from '../components/RefSelect.vue'
import ResourceView from './ResourceView.vue'
const tab=ref('import'),kind=ref(space.value==='public'?'transactions':'keywords'),source=ref<string|null>(null),file=ref<File|null>(null),sheet=ref(''),preview=ref<any>(null),loading=ref(false),error=ref(''),mapping=ref<Record<string,string>>({}),defaults=ref<Record<string,any>>({}),timezone=ref('Asia/Shanghai'),percentUnit=ref('fraction')
const batches=ref<any[]>([]),jobs=ref<any[]>([]),submitted=ref(false)
const fields=computed(()=>Object.keys(meta.value?.resources[kind.value]?.properties||{}).filter(k=>!['source_id','evidence_id'].includes(k)))
const required=computed<string[]>(()=>meta.value?.resources[kind.value]?.required||[])
const jobActive=computed(()=>jobs.value.some(j=>['RUNNING','QUEUED'].includes(j.status)))
let timer:ReturnType<typeof setInterval>|undefined
async function load(){try{batches.value=(await request('/imports')).items;jobs.value=(await request('/jobs')).items}catch(e:any){error.value=e.message}}
async function selectFile(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]||null;preview.value=null;submitted.value=false}
async function upload(){
  if(!file.value||!source.value){error.value='请先选择来源和文件';return}
  error.value='';loading.value=true
  try{
    const data=new FormData();data.append('file',file.value);data.append('source_id',source.value);data.append('kind',kind.value);data.append('sheet',sheet.value)
    preview.value=await request('/imports/preview',{method:'POST',body:data});mapping.value={};defaults.value={};submitted.value=false
    const headers=preview.value.preview.headers
    for(const field of fields.value){const alias=headers.find((h:string)=>h===field||h===labels[field]);if(alias)mapping.value[field]=alias}
    await load()
  }catch(e:any){error.value=e.message}finally{loading.value=false}
}
async function confirm(){
  if(!preview.value)return
  loading.value=true;error.value=''
  try{
    const values=Object.fromEntries(Object.entries(defaults.value).filter(([,v])=>v!==''&&v!==null&&v!==undefined))
    await post(`/imports/${preview.value.id}/confirm`,{mapping:mapping.value,defaults:values,timezone:timezone.value,percent_unit:percentUnit.value})
    submitted.value=true;ElMessage.success('已确认导入，后台任务正在处理');await load()
  }catch(e:any){error.value=e.message}finally{loading.value=false}
}
async function cancel(id:string){try{await post(`/jobs/${id}/cancel`);await load()}catch(e:any){error.value=e.message}}
watch([kind,source],()=>{preview.value=null;submitted.value=false})
watch(refresh,load)
onMounted(()=>{load();timer=setInterval(()=>{if(jobActive.value)load()},2000)})
onUnmounted(()=>clearInterval(timer))
</script>
<template>
  <section class="page-heading"><div><p class="eyebrow">数据中心</p><h1>为研究准备可靠数据</h1><p class="muted">先确认来源和字段，再预览、校验并提交导入。</p></div><el-button @click="openEditor('sources')">登记数据来源</el-button></section>
  <el-tabs v-model="tab" class="page-tabs"><el-tab-pane label="文件导入" name="import"><el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16"/>
    <div class="import-grid"><section class="panel"><div class="panel-heading"><div><span class="step-number">01</span><h3>选择数据与来源</h3></div><span class="muted">CSV / XLSX · 最大 {{meta?.max_upload_mb}}MB</span></div><el-form label-position="top"><div class="form-two"><el-form-item label="导入数据类型"><el-select v-model="kind"><el-option v-for="value in meta?.importable||[]" :key="value" :label="resources[value]?.title||value" :value="value"/></el-select></el-form-item><el-form-item label="已登记的数据来源"><RefSelect v-model="source" kind="sources"/></el-form-item></div><label class="file-drop"><el-icon><UploadFilled/></el-icon><strong>{{file?.name||'选择 CSV 或 Excel 文件'}}</strong><span>原始数据不会在预览阶段写入业务记录</span><input type="file" accept=".csv,.xlsx" @change="selectFile"/></label><el-form-item v-if="preview?.preview.sheets?.length>1" label="工作表"><el-select v-model="sheet" @change="upload"><el-option v-for="name in preview.preview.sheets" :key="name" :label="name" :value="name"/></el-select></el-form-item><div class="form-actions"><el-button @click="download('/templates/'+kind,kind+'-template.csv')" :icon="Download">标准模板</el-button><el-button type="primary" :loading="loading" @click="upload">读取并预览</el-button></div></el-form></section>
    <section class="panel import-guide"><p class="eyebrow">数据使用约定</p><h3>不同来源，各有适用范围</h3><ul><li><strong>模拟演示</strong><p>验证选品和测款完整流程，所有样例均明确标记。</p></li><li><strong>公开历史</strong><p>真实历史交易保留原币种与日期，缺少成本时不计算完整利润。</p></li><li><strong>真实经营</strong><p>导入你有权使用的数据；金额以元表示，比例单位需要确认。</p></li></ul><a href="https://archive.ics.uci.edu/dataset/352/online+retail" target="_blank" rel="noopener noreferrer">公开数据来源：UCI Online Retail ↗</a></section></div>
    <section v-if="preview" class="panel mt24"><div class="panel-heading"><div><span class="step-number">02</span><h3>检查预览与字段映射</h3><p class="muted">最多预览20行；未映射的字段可以设置统一值。</p></div><el-tag effect="plain">{{preview.filename}}</el-tag></div><el-table :data="preview.preview.rows" max-height="260"><el-table-column v-for="header in preview.preview.headers" :key="header" :label="header" :prop="header" min-width="140" show-overflow-tooltip/></el-table><div class="form-two mt24"><el-form-item label="来源时间使用的时区"><el-select v-model="timezone"><el-option label="北京时间 Asia/Shanghai" value="Asia/Shanghai"/><el-option label="英国时间 Europe/London" value="Europe/London"/><el-option label="UTC" value="UTC"/></el-select></el-form-item><el-form-item label="比例字段的原始单位"><el-select v-model="percentUnit"><el-option label="小数，如 0.08 表示 8%" value="fraction"/><el-option label="百分数，如 8 或 8%" value="percent"/></el-select></el-form-item></div><div class="mapping-grid"><div v-for="field in fields" :key="field" class="mapping-row"><label>{{labels[field]||field}}<span v-if="required.includes(field)" class="required"> *</span></label><el-select v-model="mapping[field]" clearable placeholder="选择原始数据列"><el-option v-for="header in preview.preview.headers" :key="header" :value="header" :label="header"/></el-select><RefSelect v-if="!mapping[field]&&referenceKinds[field]" v-model="defaults[field]" :kind="referenceKinds[field]"/><el-input v-else-if="!mapping[field]" v-model="defaults[field]" placeholder="统一值，未知留空"/><span v-else class="muted">从文件读取</span></div></div><div class="form-actions"><span class="muted">数据来源和原始文件摘要将随批次保存</span><el-button type="primary" :loading="loading" :disabled="submitted" @click="confirm">{{submitted?'已提交导入':'确认映射并导入'}}</el-button></div></section>
    <section class="panel mt24"><div class="panel-heading"><div><h3>最近导入批次</h3><p class="muted">失败行可下载修正，重复数据不会重复计数</p></div><el-button link @click="load">刷新</el-button></div><el-table :data="batches"><el-table-column prop="filename" label="文件" min-width="220"/><el-table-column label="类型"><template #default="{row}">{{resources[row.kind]?.title}}</template></el-table-column><el-table-column label="状态"><template #default="{row}"><el-tag :type="tagType(row.status)" effect="plain">{{stateLabel(row.status)}}</el-tag></template></el-table-column><el-table-column prop="accepted" label="已写入"/><el-table-column prop="unchanged" label="未变更"/><el-table-column prop="rejected" label="错误行"/><el-table-column label="错误报告" width="100"><template #default="{row}"><el-button v-if="row.rejected>0" link type="primary" @click="download('/imports/'+row.id+'/errors','import-errors.csv')">下载</el-button></template></el-table-column></el-table></section>
    <section v-if="jobs.length" class="panel mt24"><div class="panel-heading"><h3>后台任务</h3><span class="muted">单任务并发，降低内存占用</span></div><div v-for="job in jobs.slice(0,8)" :key="job.id" class="job-row"><div><strong>{{job.kind==='import'?'文件导入':'候选评分'}}</strong><small>{{job.error||stateLabel(job.status)}}</small></div><el-progress :percentage="job.progress" :status="job.status==='FAILED'?'exception':job.status==='SUCCEEDED'?'success':undefined"/><el-button v-if="['RUNNING','QUEUED'].includes(job.status)" link @click="cancel(job.id)">取消</el-button></div></section>
  </el-tab-pane><el-tab-pane label="数据来源" name="sources"><ResourceView resource-kind="sources" embedded/></el-tab-pane><el-tab-pane label="证据档案" name="evidence"><ResourceView resource-kind="evidence" embedded/></el-tab-pane><el-tab-pane label="公开历史交易" name="history"><ResourceView resource-kind="transactions" embedded/></el-tab-pane></el-tabs>
</template>
