<script setup lang="ts">
/**
 * 仪表盘：四个统计卡片 + 最近文章 + 最近运行记录。
 */

import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'

import RunStatus from '@/components/RunStatus.vue'
import { ApiError } from '@/api/http'
import { useArticleStore } from '@/stores/article'
import { ARTICLE_STATUS_LABELS, type ArticleStatus } from '@/types/article'
import { formatDateTime } from '@/utils/format'

const router = useRouter()
const articleStore = useArticleStore()
const { stats, statsLoading } = storeToRefs(articleStore)

/** 最近文章列表（stats 还没加载时给个空数组，模板里就不用反复判空） */
const recentArticles = computed(() => stats.value?.recent_articles ?? [])
/** 最近运行记录 */
const recentRuns = computed(() => stats.value?.recent_runs ?? [])

/** 文章状态 -> 标签颜色 */
function statusTagType(status: ArticleStatus): 'primary' | 'success' | 'info' | 'warning' {
  if (status === 'approved') {
    return 'success'
  }
  if (status === 'review') {
    return 'warning'
  }
  if (status === 'archived') {
    return 'info'
  }
  return 'primary'
}

/** 打开文章详情 */
function openArticle(id: string): void {
  void router.push({ name: 'article-detail', params: { id } })
}

onMounted(async () => {
  try {
    await articleStore.fetchStats()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载统计数据失败')
  }
})
</script>

<template>
  <div v-loading="statsLoading" class="dashboard">
    <!-- 顶部：标题 + 创建按钮 -->
    <div class="dashboard-header">
      <h2 class="dashboard-title">仪表盘</h2>
      <el-button type="primary" @click="router.push('/create')">创建新文章</el-button>
    </div>

    <!-- 四个统计卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">总文章数</div>
          <div class="stat-value">{{ stats?.total ?? 0 }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">草稿</div>
          <div class="stat-value stat-value--draft">{{ stats?.draft ?? 0 }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">待审</div>
          <div class="stat-value stat-value--review">{{ stats?.review ?? 0 }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">已通过</div>
          <div class="stat-value stat-value--approved">{{ stats?.approved ?? 0 }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近文章 -->
    <el-card shadow="never" class="table-card">
      <template #header>
        <div class="card-header">
          <span>最近文章</span>
          <el-button link type="primary" @click="router.push('/articles')">查看全部</el-button>
        </div>
      </template>

      <el-table :data="recentArticles" empty-text="还没有文章，点击右上角「创建新文章」开始吧">
        <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ ARTICLE_STATUS_LABELS[row.status as ArticleStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" width="160" show-overflow-tooltip />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click="openArticle(row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 最近运行记录 -->
    <el-card shadow="never" class="table-card">
      <template #header>
        <span>最近 Agent 运行</span>
      </template>

      <el-table :data="recentRuns" empty-text="还没有运行记录">
        <el-table-column prop="run_id" label="运行 ID" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="220">
          <template #default="{ row }">
            <RunStatus
              :status="row.status"
              :tool-call-count="row.tool_call_count"
              :error-message="row.error_message"
            />
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" width="160" show-overflow-tooltip />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.dashboard-title {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.stat-row {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
}

.stat-label {
  font-size: 13px;
  color: #909399;
}

.stat-value {
  margin-top: 8px;
  font-size: 28px;
  font-weight: 600;
  color: #303133;
}

.stat-value--draft {
  color: #409eff;
}

.stat-value--review {
  color: #e6a23c;
}

.stat-value--approved {
  color: #67c23a;
}

.table-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
