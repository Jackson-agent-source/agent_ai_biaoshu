<script setup lang="ts">
/**
 * 设置页：只读展示当前用户信息和后端地址。
 * 这个项目里前端不保存任何密钥，所以这里没有可以编辑的配置项。
 */

import { computed } from 'vue'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

/**
 * 后端 API 地址（来自 .env 里的 VITE_API_BASE_URL）。
 *
 * 兜底值和 src/api/http.ts 保持一致，用相对路径 —— 线上是前后端同源部署，
 * 写死 localhost 的话这个页面会把访问者自己的电脑显示成"后端地址"，很误导人。
 */
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '/api/v1')

/** 前端版本号，从 package.json 里读比较麻烦，这里直接写死一个展示值 */
const APP_VERSION = '0.1.0'
</script>

<template>
  <div class="settings-page">
    <div class="page-header">
      <h2 class="page-title">设置</h2>
    </div>

    <!-- 当前用户 -->
    <el-card shadow="never" class="settings-card">
      <template #header>
        <span>当前用户</span>
      </template>

      <el-descriptions :column="1" border>
        <el-descriptions-item label="用户名">
          {{ auth.user?.username || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="邮箱">
          {{ auth.user?.email || '未填写' }}
        </el-descriptions-item>
        <el-descriptions-item label="用户 ID">
          {{ auth.user?.id ?? '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="管理员">
          <el-tag :type="auth.user?.is_staff ? 'success' : 'info'" size="small">
            {{ auth.user?.is_staff ? '是' : '否' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 后端地址 -->
    <el-card shadow="never" class="settings-card">
      <template #header>
        <span>后端接口</span>
      </template>

      <el-descriptions :column="1" border>
        <el-descriptions-item label="API 地址">
          <span class="mono">{{ apiBaseUrl }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="认证方式">
          DRF Token（登录后自动带上 Authorization: Token &lt;token&gt;）
        </el-descriptions-item>
      </el-descriptions>

      <el-alert type="info" :closable="false" class="settings-alert">
        <template #title>关于密钥</template>
        <template #default>
          API Key 全部保存在后端的 <code>.env</code> 文件里，前端不接触任何密钥。
          前端只保存一个登录用的 token（在浏览器 localStorage 中），退出登录就会清除。
        </template>
      </el-alert>

      <el-alert type="warning" :closable="false" class="settings-alert">
        <template #title>改地址怎么办？</template>
        <template #default>
          修改 <code>frontend/.env</code> 里的 <code>VITE_API_BASE_URL</code>，
          然后重启 <code>npm run dev</code> 即可生效。
        </template>
      </el-alert>
    </el-card>

    <!-- 关于 -->
    <el-card shadow="never" class="settings-card">
      <template #header>
        <span>关于</span>
      </template>

      <el-descriptions :column="1" border>
        <el-descriptions-item label="项目名称">
          微信公众号 AI Content Agent
        </el-descriptions-item>
        <el-descriptions-item label="前端版本">{{ APP_VERSION }}</el-descriptions-item>
        <el-descriptions-item label="技术栈">
          Vue 3（组合式 API）+ Vite + TypeScript + Vue Router 4 + Pinia + Axios +
          Element Plus + markdown-it + DOMPurify
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.settings-card {
  margin-bottom: 16px;
  max-width: 900px;
}

.settings-alert {
  margin-top: 16px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 13px;
}

code {
  background-color: #f5f7fa;
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
