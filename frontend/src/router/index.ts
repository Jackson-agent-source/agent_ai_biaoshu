/**
 * 路由配置。
 *
 * 除了 /login 之外，其它页面都必须登录后才能访问，
 * 这个判断放在下面的全局前置守卫里统一处理。
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import MainLayout from '@/layouts/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'

// 给路由 meta 加上类型，这样写 to.meta.title 时有提示，不会变成 unknown
declare module 'vue-router' {
  interface RouteMeta {
    /** 是否是不需要登录就能访问的公开页面 */
    public?: boolean
    /** 页面标题，会显示在浏览器标签上 */
    title?: string
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    // 其余页面都套在 MainLayout 里（左侧菜单 + 顶栏）
    path: '/',
    component: MainLayout,
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/pages/Dashboard.vue'),
        meta: { title: '仪表盘' },
      },
      {
        path: 'create',
        name: 'create-article',
        component: () => import('@/pages/CreateArticle.vue'),
        meta: { title: '创建文章' },
      },
      {
        path: 'articles',
        name: 'article-history',
        component: () => import('@/pages/ArticleHistory.vue'),
        meta: { title: '文章历史' },
      },
      {
        path: 'articles/:id',
        name: 'article-detail',
        component: () => import('@/pages/ArticleDetail.vue'),
        meta: { title: '文章详情' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/pages/Settings.vue'),
        meta: { title: '设置' },
      },
    ],
  },
  {
    // 兜底：随便输的地址都送回首页
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

/** 全局前置守卫：没登录就送去登录页；已登录还想去登录页就送回首页 */
router.beforeEach((to) => {
  const auth = useAuthStore()

  // 已经登录了还访问 /login，直接回首页
  if (to.meta.public && auth.isLoggedIn) {
    return { name: 'dashboard' }
  }

  // 需要登录但没登录，跳转登录页并记住原本想去的地址
  if (!to.meta.public && !auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  return true
})

// 每次切页面时顺手改一下浏览器标签标题
router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - AI Content Agent` : 'AI Content Agent'
})

export default router
