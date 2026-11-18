import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
const router = createRouter({history:createWebHashHistory(),routes:[
  {path:'/', component:()=>import('./views/Dashboard.vue')}, {path:'/data', component:()=>import('./views/DataCenter.vue')}, {path:'/profit', component:()=>import('./views/ProfitView.vue')},
  {path:'/discover', component:()=>import('./views/Discovery.vue')},
  {path:'/settings', component:()=>import('./views/Settings.vue')}, {path:'/records/:kind', component:()=>import('./views/ResourceView.vue')},
]})
createApp(App).use(router).use(ElementPlus,{locale:zhCn}).mount('#app')
