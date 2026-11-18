<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { DataAnalysis, Search, Goods, Box, Coin, TrendCharts, Setting, Upload, ArrowRight, SwitchButton, DocumentChecked, Connection } from '@element-plus/icons-vue'
import { meta, post, request, sessionReady, space, refresh } from './api/client'
import { resources } from './catalog'
import RecordForm from './components/RecordForm.vue'
const route = useRoute(), router = useRouter()
const password = ref(''), loading = ref(false), loginError = ref(''), checking = ref(true)
const menus = [
  {label:'经营工作台', path:'/', icon:DataAnalysis}, {label:'选品中心', path:'/records/candidates', icon:Search},
  {label:'关键词研究', path:'/records/keywords', icon:TrendCharts}, {label:'商品与比价', path:'/records/products', icon:Goods},
  {label:'货源与样品', path:'/records/suppliers', icon:Box}, {label:'利润测算', path:'/profit', icon:Coin},
  {label:'风险审核', path:'/records/risks', icon:DocumentChecked}, {label:'测款与复盘', path:'/records/experiments', icon:Connection},
  {label:'数据中心', path:'/data', icon:Upload}, {label:'设置与审计', path:'/settings', icon:Setting},
]
const title = computed(() => resources[String(route.params.kind)]?.title || menus.find(x => x.path===route.path)?.label || '工作台')
async function init() {
  try { await request('/auth/me'); sessionReady.value = true; meta.value = await request('/meta') }
  catch { sessionReady.value = false } finally { checking.value = false }
}
async function login() {
  loading.value = true; loginError.value = ''
  try { await post('/auth/login',{password:password.value}); password.value=''; await init() }
  catch(e:any) { loginError.value = e.message } finally { loading.value=false }
}
async function logout() { await post('/auth/logout'); sessionReady.value=false }
watch(space, value => { localStorage.setItem('commerceai-space',value); refresh.value++; ElMessage.info('已切换到'+(meta.value?.workspaces[value] || value)) })
onMounted(init)
</script>
<template>
  <div v-if="checking" class="boot-screen">正在连接本机工作台…</div>
  <div v-else-if="!sessionReady" class="login-page">
    <div class="login-story"><div class="brand light"><span class="brand-mark">C</span> Commerce<span>AI</span></div><div><p class="eyebrow">本地电商研究工作台</p><h1>研究商品机会<br>记录经营结果</h1><p>从关键词、同款价格到供应链和真实订单，<br>让每次选品都有可以追溯的依据。</p><div class="login-steps"><span>机会研究</span><span>经济性核验</span><span>测款复盘</span></div></div><small>CommerceAI 1.0 · 数据保存在本机</small></div>
    <div class="login-panel"><div class="login-box"><p class="eyebrow">欢迎回来</p><h2>打开你的经营工作台</h2><p class="muted">使用初始化时生成的本机访问口令登录。</p><el-alert v-if="loginError" :title="loginError" type="error" :closable="false" class="mb16"/><el-form @submit.prevent="login" label-position="top"><el-form-item label="访问口令"><el-input v-model="password" type="password" show-password autocomplete="current-password" size="large" placeholder="输入本机访问口令" /></el-form-item><el-button type="primary" size="large" native-type="submit" :loading="loading" style="width:100%">进入工作台 <el-icon><ArrowRight/></el-icon></el-button></el-form><p class="login-help">口令保存在项目目录的 <code>.local/admin-password</code> 文件中。</p></div></div>
  </div>
  <div v-else class="shell">
    <aside class="sidebar"><router-link to="/" class="brand light"><span class="brand-mark">C</span>Commerce<span>AI</span></router-link><div class="sidebar-label">研究与经营</div><nav><router-link v-for="item in menus" :key="item.path" :to="item.path" :class="{active:route.path===item.path}"><el-icon><component :is="item.icon"/></el-icon><span>{{item.label}}</span><i v-if="route.path===item.path"/></router-link></nav><div class="sidebar-footer"><span class="status-dot"/><div><strong>本机工作空间</strong><small>单人使用 · 数据独立保存</small></div></div></aside>
    <div class="main-shell"><header class="topbar"><div class="breadcrumb">工作空间 <span>/</span><strong>{{title}}</strong></div><div class="topbar-actions"><el-select v-model="space" aria-label="数据空间" class="space-select"><el-option label="模拟演示" value="demo"/><el-option label="公开历史" value="public"/><el-option label="真实经营" value="live"/></el-select><el-button class="import-top" @click="router.push('/data')"><el-icon><Upload/></el-icon>导入数据</el-button><el-tooltip content="退出本机会话"><el-button circle @click="logout" aria-label="退出登录"><el-icon><SwitchButton/></el-icon></el-button></el-tooltip><span class="avatar">Y</span></div></header>
    <div class="space-banner" :class="space"><span class="status-dot"/><span v-if="space==='demo'">模拟演示空间 <strong>所有商品、报价与经营结果均为模拟数据</strong></span><span v-else-if="space==='public'">公开历史空间 <strong>历史数据保留原币种和日期，仅用于离线研究</strong></span><span v-else>真实经营空间 <strong>仅使用你有权访问并核验的数据</strong></span></div>
    <main class="page"><router-view :key="space"/></main></div>
    <RecordForm />
  </div>
</template>
