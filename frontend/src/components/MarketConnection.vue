<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { post, put } from '../api/client'
import { platformNames, type ConnectionInput, type MarketConnection, type Platform } from '../api/market'

const props = defineProps<{ modelValue: boolean; connection: MarketConnection | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; saved: [value: MarketConnection] }>()
const busy = ref(false), testing = ref(false), error = ref('')
const tests = ref<{ platform: Platform; ok: boolean; message: string }[]>([])
const form = reactive({ enabled: false, api_key: '', api_secret: '', access_token: '',
  taobao_route: 'tglobal' as 'tglobal' | 'taobao', platforms: Object.keys(platformNames) as Platform[], permission_note: '' })
watch(() => props.modelValue, open => {
  if (!open) return
  Object.assign(form, { enabled: props.connection?.enabled || false, api_key: '', api_secret: '', access_token: '',
    taobao_route: props.connection?.taobao_route || 'tglobal', platforms: props.connection?.platforms || Object.keys(platformNames),
    permission_note: props.connection?.permission_note || '' })
  tests.value = []; error.value = ''
})
async function save(clear = false) {
  busy.value = true; error.value = ''
  try {
    const payload: ConnectionInput = { ...form, enabled: clear ? false : form.enabled,
      api_key: clear ? '' : form.api_key || null, api_secret: clear ? '' : form.api_secret || null,
      access_token: clear ? '' : form.access_token || null }
    const value = await put('/market/connection', payload)
    emit('saved', value); emit('update:modelValue', false)
    ElMessage.success(clear ? '密钥已清除，连接已停用' : '连接配置已保存，未发起商品查询')
  } catch (e: any) { error.value = e.message }
  finally { busy.value = false }
}
async function test() {
  testing.value = true; error.value = ''; tests.value = []
  try { tests.value = (await post('/market/connection/test', { query: '牙刷' })).results }
  catch (e: any) { error.value = e.message }
  finally { testing.value = false }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @close="emit('update:modelValue', false)" title="平台数据连接" width="760px">
    <el-alert title="当前默认免费使用，数据服务待开通" type="info" :closable="false" show-icon class="mb16"
      description="自动查询默认关闭，需要有效的数据接口凭据。未开通时可免费检索并录入平台报价；开通后请按实际账号确认平台权限和费用。" />
    <p class="muted mb16">首个适配服务：OneBound 万邦。它是第三方数据服务，平台权限、数据覆盖和费用以你的实际账号为准。
      <a href="https://open.onebound.cn/help/api/" target="_blank" rel="noopener noreferrer" class="positive">查看服务商文档</a>
    </p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb16" />
    <el-form label-position="top" class="record-form">
      <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password autocomplete="new-password"
        :placeholder="connection?.configured ? '已保存，留空保留' : '开通服务后填写，当前可以留空'" /></el-form-item>
      <el-form-item label="API Secret"><el-input v-model="form.api_secret" type="password" show-password autocomplete="new-password"
        :placeholder="connection?.has_secret ? '已保存，留空保留' : '按服务商账号要求填写'" /></el-form-item>
      <el-form-item label="淘宝接口版本"><el-select v-model="form.taobao_route" style="width:100%">
        <el-option label="淘宝海外 tglobal（服务商当前文档）" value="tglobal" />
        <el-option label="旧淘宝 taobao（仅已开通对应权限账号）" value="taobao" />
      </el-select></el-form-item>
      <el-form-item label="淘宝授权 Token"><el-input v-model="form.access_token" type="password" show-password autocomplete="new-password"
        placeholder="按淘宝详情接口授权要求填写；留空保留已有值" /></el-form-item>
      <el-form-item label="启用的平台" class="full"><el-checkbox-group v-model="form.platforms">
        <el-checkbox v-for="(name, key) in platformNames" :key="key" :value="key">{{ name }}</el-checkbox>
      </el-checkbox-group></el-form-item>
      <el-form-item label="数据使用范围说明" class="full"><el-input v-model="form.permission_note" type="textarea" :rows="2"
        placeholder="开通后记录账号允许使用的数据范围；不要在此填写密钥" /></el-form-item>
      <el-form-item label="开通后主动启用自动查询" class="full"><el-switch v-model="form.enabled" />
        <span class="muted" style="margin-left:12px">启用前请确认平台权限及计费。保存设置本身不调用外部接口。</span>
      </el-form-item>
    </el-form>
    <el-table v-if="tests.length" :data="tests" class="mb16">
      <el-table-column label="平台" width="120"><template #default="{ row }">{{ platformNames[row.platform as Platform] }}</template></el-table-column>
      <el-table-column label="结果" width="100"><template #default="{ row }"><el-tag :type="row.ok ? 'success' : 'warning'">{{ row.ok ? '响应成功' : '未联通' }}</el-tag></template></el-table-column>
      <el-table-column prop="message" label="说明" />
    </el-table>
    <p class="muted">密钥只保存在本机私有配置中，读取接口不返回密钥；数据库与证据备份默认不包含这些密钥。</p>
    <template #footer>
      <el-button v-if="connection?.configured" @click="save(true)" :loading="busy">清除密钥并停用</el-button>
      <el-button @click="test" :disabled="!connection?.configured || !connection?.enabled" :loading="testing">测试连接（最多4次搜索）</el-button>
      <el-button type="primary" @click="save(false)" :loading="busy">保存配置</el-button>
    </template>
  </el-dialog>
</template>
