<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { editor, meta, request, post, put, refresh, referenceKinds } from '../api/client'
import { labels, resources, stateLabel } from '../catalog'
import RefSelect from './RefSelect.vue'
const values = reactive<Record<string, any>>({}), busy = ref(false), error = ref('')
const fields = computed(() => Object.entries(meta.value?.resources[editor.value?.kind || '']?.properties || {}) as [string, any][])
const required = computed<string[]>(() => meta.value?.resources[editor.value?.kind || '']?.required || [])
function schema(p: any) { return p.anyOf?.find((x:any) => x.type !== 'null') || p }
function nullable(p:any) { return p.anyOf?.some((x:any) => x.type === 'null') }
watch(editor, async (ed) => {
  Object.keys(values).forEach(k => delete values[k]); error.value = ''
  if (!ed) return
  for (const [key, property] of fields.value) {
    let value = ed.row?.data[key] ?? ed.values?.[key] ?? property.default
    const p = schema(property)
    if (value === undefined && ['observed_at', 'paid_at', 'start_at', 'invoice_at'].includes(key)) value = new Date().toISOString()
    if (value === undefined && ['expires_at', 'end_at'].includes(key) && required.value.includes(key)) value = new Date(Date.now()+14*864e5).toISOString()
    if (value === undefined && p.type === 'object') value = key === 'attributes' ? {material:'',size:''} : {}
    if (value === undefined && p.type === 'array') value = []
    if (value === undefined && p.type === 'boolean') value = nullable(property) ? null : false
    values[key] = p.type === 'object' ? JSON.stringify(value ?? {}, null, 2) : value ?? null
  }
  if (ed.kind === 'strategies' && !ed.row && (!ed.values?.config)) {
    const existing = await request('/strategies?page_size=1')
    if (existing.items[0]) {
      values.config = JSON.stringify(existing.items[0].data.config, null, 2)
      values.version = 'selection-' + new Date().toISOString().replace(/[-:.TZ]/g,'').slice(0,14)
    }
  }
})
async function submit() {
  if (!editor.value) return
  error.value = ''; busy.value = true
  try {
    const payload: Record<string,any> = {}
    for (const [key, property] of fields.value) {
      let value = values[key]; const p = schema(property)
      if (value === '' || value === null || value === undefined) {
        if (required.value.includes(key)) throw new Error((labels[key] || key)+'不能为空')
        if (nullable(property)) payload[key] = null
        continue
      }
      if (p.type === 'object') value = JSON.parse(value)
      if (p.format === 'date-time') value = new Date(value).toISOString()
      if (p.type === 'integer' || p.type === 'number') value = Number(value)
      if (p.type === 'string' && typeof value === 'number') value = String(value)
      payload[key] = value
    }
    const ed = editor.value
    if (ed.row) await put(`/${ed.kind}/${ed.row.id}`, {expected_version: ed.row.version, payload})
    else await post(`/${ed.kind}`, payload)
    ElMessage.success(ed.row ? '修改已保存，历史版本已保留' : '记录已保存')
    editor.value = null; refresh.value++
  } catch (e:any) {
    const details = e.details?.errors?.map((x:any) => `${labels[x.loc?.at(-1)] || x.loc?.at(-1)}：${x.message}`).join('；')
    error.value = details || e.message
  } finally { busy.value = false }
}
</script>
<template>
  <el-dialog :model-value="!!editor" @close="editor=null" :title="`${editor?.row ? '修改' : '新增'}${resources[editor?.kind || '']?.title || ''}`" width="760px" destroy-on-close>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon class="mb16" />
    <el-alert v-if="editor?.kind==='orders'" title="未知费用请留空；确认没有发生的费用才填写 0。外部订单标识保存时会脱敏。" type="info" :closable="false" class="mb16" />
    <el-alert v-if="editor?.kind==='assessments'" title="人工分项 0–100 分，越高越好。缺失的依据留空；评分不会解除样品或风险阻断。" type="info" :closable="false" class="mb16" />
    <el-form label-position="top" @submit.prevent="submit" class="record-form">
      <el-form-item v-for="[key, property] in fields" :key="key" :label="(labels[key] || key) + (['conversion','mall_click_share','r','head_share'].includes(key) ? '（0–1）' : '')" :required="required.includes(key)" :class="{'full':schema(property).type==='object' || ['note','reason','hypothesis','permission_note','return_terms'].includes(key)}">
        <RefSelect v-if="referenceKinds[key]" v-model="values[key]" :kind="referenceKinds[key]" />
        <el-select v-else-if="schema(property).enum" v-model="values[key]" :clearable="nullable(property)" style="width:100%"><el-option v-for="option in schema(property).enum" :key="option" :label="stateLabel(option)" :value="option" /></el-select>
        <el-select v-else-if="schema(property).type==='array'" v-model="values[key]" multiple filterable allow-create style="width:100%"><el-option label="全部数据类型" value="*"/><el-option v-for="(r,kind) in resources" :key="kind" :label="r.title" :value="kind"/></el-select>
        <el-select v-else-if="schema(property).type==='boolean' && nullable(property)" v-model="values[key]" clearable style="width:100%"><el-option label="是" :value="true"/><el-option label="否" :value="false"/></el-select>
        <el-switch v-else-if="schema(property).type==='boolean'" v-model="values[key]" />
        <el-date-picker v-else-if="schema(property).format==='date-time'" v-model="values[key]" type="datetime" style="width:100%" placeholder="选择时间" />
        <el-date-picker v-else-if="key==='business_date'" v-model="values[key]" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        <el-input v-else-if="schema(property).type==='object'" v-model="values[key]" type="textarea" :rows="key==='config' ? 16 : 4" class="code-input" spellcheck="false" />
        <el-input-number v-else-if="['number','integer'].includes(schema(property).type)" v-model="values[key]" :min="schema(property).minimum" :max="schema(property).maximum" :controls="false" style="width:100%" />
        <el-input v-else v-model="values[key]" :type="['note','reason','hypothesis','permission_note','return_terms'].includes(key) ? 'textarea' : 'text'" :rows="2" :placeholder="nullable(property) ? '未知请留空' : ''" />
      </el-form-item>
    </el-form>
    <template #footer><el-button @click="editor=null">取消</el-button><el-button type="primary" :loading="busy" @click="submit">保存记录</el-button></template>
  </el-dialog>
</template>
