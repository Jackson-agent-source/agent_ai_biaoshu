<script setup lang="ts">
/**
 * 主布局：左边是菜单，上面是顶栏，中间是具体页面。
 * 结构用的是 Element Plus 的 el-container / el-aside / el-header / el-main。
 */

import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, EditPen, Files, Setting, SwitchButton } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

/** 当前高亮的菜单项：直接用路由路径 */
const activeMenu = computed(() => route.path)

/** 顶栏显示的用户名 */
const displayName = computed(() => auth.user?.username || '未登录')

// 进入布局时拉一次用户信息（刷新页面后 store 里只有 token，没有用户名）
onMounted(() => {
  if (auth.isLoggedIn && !auth.user) {
    auth.fetchMe().catch(() => {
      // 失败一般说明 token 过期了，拦截器已经处理跳转，这里不用再提示
    })
  }
})

/** 退出登录 */
async function handleLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    // 用户点了取消，什么都不做
    return
  }

  await auth.logout()
  ElMessage.success('已退出登录')
  void router.push({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <!-- 左侧菜单 -->
    <el-aside width="200px" class="layout-aside">
      <div class="logo">AI 内容助手</div>
      <el-menu :default-active="activeMenu" router class="layout-menu">
        <el-menu-item index="/">
          <el-icon><Document /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/create">
          <el-icon><EditPen /></el-icon>
          <span>创建文章</span>
        </el-menu-item>
        <el-menu-item index="/articles">
          <el-icon><Files /></el-icon>
          <span>文章历史</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶栏 -->
      <el-header class="layout-header">
        <div class="header-title">微信公众号 AI Content Agent</div>
        <div class="header-right">
          <span class="header-user">你好，{{ displayName }}</span>
          <el-button type="primary" link :icon="SwitchButton" @click="handleLogout">
            退出登录
          </el-button>
        </div>
      </el-header>

      <!-- 页面内容 -->
      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}

.layout-aside {
  background-color: #ffffff;
  border-right: 1px solid #e4e7ed;
}

.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-size: 16px;
  font-weight: 600;
  color: #409eff;
  border-bottom: 1px solid #e4e7ed;
}

.layout-menu {
  border-right: none;
}

.layout-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: #ffffff;
  border-bottom: 1px solid #e4e7ed;
}

.header-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-user {
  font-size: 14px;
  color: #606266;
}

.layout-main {
  background-color: #f5f7fa;
  padding: 20px;
}
</style>
