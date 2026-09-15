<script setup lang="ts">
/**
 * 文章详情页。
 * 左边是编辑器，右边是三个信息 tab：资料来源 / Agent 信息 / 风险提示。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'

import ArticleEditor from '@/components/ArticleEditor.vue'
import RunStatus from '@/components/RunStatus.vue'
import SourceList from '@/components/SourceList.vue'
import { getRun } from '@/api/agent'
import { ApiError } from '@/api/http'
import { useArticleStore } from '@/stores/article'
import { ARTICLE_STATUS_LABELS, type Article, type ArticleStatus } from '@/types/article'
import type { AgentRun } from '@/types/agent'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const articleStore = useArticleStore()
const { current: article, detailLoading } = storeToRefs(articleStore)

/** 当前文章 id（来自地址栏） */
const articleId = computed(() => String(route.params.id ?? ''))

/** 这篇文章对应的 Agent 运行记录（用来显示运行状态） */
const agentRun = ref<AgentRun | null>(null)

/** 状态下拉选项 */
const STATUS_OPTIONS: { label: string; value: ArticleStatus }[] = [
  { label: '草稿', value: 'draft' },
  { label: '待审', value: 'review' },
  { label: '已通过', value: 'approved' },
  { label: '已归档', value: 'archived' },
]

/** 状态标签颜色 */
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

/** 加载文章详情 */
async function loadArticle(): Promise<void> {
  try {
    const data = await articleStore.fetchDetail(articleId.value)
    await loadAgentRun(data?.agent_run_id ?? null)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载文章失败')
  }
}

/** 加载对应的运行记录；没有就置空 */
async function loadAgentRun(runId: string | null): Promise<void> {
  if (!runId) {
    agentRun.value = null
    return
  }
  try {
    agentRun.value = await getRun(runId)
  } catch {
    // 运行记录拿不到不影响看文章，静默处理
    agentRun.value = null
  }
}

/** 编辑器保存成功后，用返回的数据刷新页面上的文章对象 */
function handleSaved(updated: Article): void {
  articleStore.current = updated
}

/** 修改文章状态 */
async function handleStatusChange(status: ArticleStatus): Promise<void> {
  if (!article.value || article.value.status === status) {
    return
  }
  try {
    const updated = await articleStore.update(article.value.id, { status })
    articleStore.current = updated
    ElMessage.success('状态已更新')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '更新状态失败')
  }
}

/** 归档（软删除） */
async function handleArchive(): Promise<void> {
  if (!article.value) {
    return
  }
  try {
    await ElMessageBox.confirm('确定要归档这篇文章吗？（归档后仍可在列表中查看）', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await articleStore.remove(article.value.id)
    ElMessage.success('已归档')
    await router.push('/articles')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '归档失败')
  }
}

onMounted(() => {
  void loadArticle()
})

// 地址栏里的 id 变了（比如从列表点进另一篇），重新加载
watch(articleId, () => {
  void loadArticle()
})
</script>

<template>
  <div v-loading="detailLoading" class="detail-page">
    <!-- 顶部：返回 + 状态 + 操作 -->
    <div class="page-header">
      <div class="header-left">
        <el-button @click="router.back()">返回</el-button>
        <h2 class="page-title">{{ article?.title || '文章详情' }}</h2>
        <el-tag v-if="article" :type="statusTagType(article.status)">
          {{ ARTICLE_STATUS_LABELS[article.status] }}
        </el-tag>
        <el-tag v-if="article" type="info" size="small">v{{ article.version }}</el-tag>
      </div>

      <div class="header-right">
        <el-select
          v-if="article"
          :model-value="article.status"
          class="status-select"
          @change="handleStatusChange"
        >
          <el-option
            v-for="option in STATUS_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button v-if="article" type="danger" plain @click="handleArchive">归档</el-button>
      </div>
    </div>

    <el-empty v-if="!article && !detailLoading" description="文章不存在或已被删除" />

    <el-row v-if="article" :gutter="16">
      <!-- 左：编辑器 -->
      <el-col :xs="24" :lg="15">
        <ArticleEditor :article="article" @saved="handleSaved" />
      </el-col>

      <!-- 右：信息区 -->
      <el-col :xs="24" :lg="9">
        <el-card shadow="never">
          <el-tabs>
            <!-- 资料来源 -->
            <el-tab-pane label="资料来源">
              <SourceList :sources="article.sources" />
            </el-tab-pane>

            <!-- Agent 信息 -->
            <el-tab-pane label="Agent 信息">
              <el-descriptions :column="1" border>
                <el-descriptions-item label="使用模型">
                  {{ article.model_name || '-' }}
                </el-descriptions-item>
                <el-descriptions-item label="工具调用次数">
                  {{ article.tool_call_count }}
                </el-descriptions-item>
                <el-descriptions-item label="运行状态">
                  <RunStatus
                    v-if="agentRun"
                    :status="agentRun.status"
                    :tool-call-count="agentRun.tool_call_count"
                    :error-message="agentRun.error_message"
                  />
                  <el-tag v-else type="info">暂无运行记录</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="运行 ID">
                  <span class="mono">{{ article.agent_run_id || '-' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="会话 ID">
                  <span class="mono">{{ article.conversation_id || '-' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="创建时间">
                  {{ formatDateTime(article.created_at) }}
                </el-descriptions-item>
                <el-descriptions-item label="更新时间">
                  {{ formatDateTime(article.updated_at) }}
                </el-descriptions-item>
              </el-descriptions>

              <!-- 大纲和配图建议也放在这里，方便参考 -->
              <div v-if="article.outline && article.outline.length > 0" class="sub-block">
                <div class="sub-title">文章大纲</div>
                <ol class="outline-list">
                  <li v-for="(line, index) in article.outline" :key="index">{{ line }}</li>
                </ol>
              </div>

              <div v-if="article.image_suggestions && article.image_suggestions.length > 0" class="sub-block">
                <div class="sub-title">配图建议</div>
                <ul class="outline-list">
                  <li v-for="(line, index) in article.image_suggestions" :key="index">{{ line }}</li>
                </ul>
              </div>
            </el-tab-pane>

            <!-- 风险提示 -->
            <el-tab-pane label="风险提示">
              <ul v-if="article.risk_notes && article.risk_notes.length > 0" class="risk-list">
                <li v-for="(note, index) in article.risk_notes" :key="index">{{ note }}</li>
              </ul>
              <el-empty v-else description="无风险提示" :image-size="70" />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.status-select {
  width: 120px;
}

.mono {
  font-size: 12px;
  word-break: break-all;
}

.sub-block {
  margin-top: 16px;
}

.sub-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.outline-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  color: #606266;
  line-height: 1.9;
}

.risk-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  color: #e6a23c;
  line-height: 1.9;
}
</style>
