/**
 * 应用入口。
 * 这里把 Pinia（状态管理）、Router（路由）、Element Plus（UI 组件库）装到 Vue 上。
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
// Element Plus 的中文语言包，装上之后分页、日期等组件都会显示中文
import zhCn from 'element-plus/es/locale/lang/zh-cn'
// Element Plus 的样式文件
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

// 注意顺序：Pinia 要在 Router 之前安装，
// 因为路由守卫里会用到 store。
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
