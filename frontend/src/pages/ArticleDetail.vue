<script setup lang="ts">
/**
 * 文章详情页。
 * 左边是编辑器，右边是四个信息 tab：资料来源 / 配图 / Agent 信息 / 风险提示。
 *
 * 页头还有一个「推送到公众号草稿箱」按钮 —— 它依赖配图，
 * 没有可用配图时会被置灰并说明原因（见 canPush / pushHint）。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'

import ArticleEditor from '@/components/ArticleEditor.vue'
import ArticleImagePanel from '@/components/ArticleImagePanel.vue'
import RunStatus from '@/components/RunStatus.vue'
import SourceList from '@/components/SourceList.vue'
import { getRun } from '@/api/agent'
import { ApiError } from '@/api/http'
import { listWeChatPublishes, publishToWeChat } from '@/api/wechat'
import { useArticleStore } from '@/stores/article'
import { useImageStore } from '@/stores/image'
import { ARTICLE_STATUS_LABELS, type Article, type ArticleStatus } from '@/types/article'
import type { AgentRun } from '@/types/agent'
import {
  WECHAT_PUBLISH_STATUS_TAG_TYPES,
  type WeChatPublish,
} from '@/types/wechat'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const articleStore = useArticleStore()
const imageStore = useImageStore()
const { current: article, detailLoading } = storeToRefs(articleStore)

/** 当前文章 id（来自地址栏） */
const articleId = computed(() => String(route.params.id ?? ''))

/** 这篇文章对应的 Agent 运行记录（用来显示运行状态） */
const agentRun = ref<AgentRun | null>(null)

/**
 * 当前选中的 tab。
 * ★ 默认停在「资料来源」，但如果地址栏带了 ?tab=images 就直接打开「配图」——
 *   创建文章页在用户回答"要现在生成配图吗"时点"去生成配图"就会带上这个参数。
 *   不带的话用户跳过来还得自己找那个 tab，等于白问了一句。
 */
const TABS = ['sources', 'images', 'agent', 'risks'] as const
const activeTab = ref<string>('sources')

/** 最近一次推送记录（页头那句「上次推送」用的），null = 从来没推过 */
const lastPublish = ref<WeChatPublish | null>(null)
/** 正在推送 */
const publishing = ref(false)

/**
 * 能不能推送。
 * ★ 这是**硬性前提**，不是"建议"：微信的 draft/add 要求 thumb_media_id 必填，
 *   后端 services 里第一件事就是检查有没有成功生成的配图，没有直接报错。
 *   所以这里直接置灰，而不是让用户点一下、等几秒、再吃一个错误。
 */
const canPush = computed(() => imageStore.succeededImages.length > 0)

/** 按钮为什么是灰的。禁用时用 tooltip 显示 —— 置灰但不给理由等于坏了 */
const pushHint = computed(() =>
  canPush.value
    ? ''
    : '公众号要求草稿必须有封面图，请先在下面的「配图」里生成至少一张配图。',
)

/** 推送会更新还是新建。用来写确认框里的那句话 */
const pushWillUpdate = computed(
  () => lastPublish.value?.status === 'succeeded' && Boolean(lastPublish.value?.media_id),
)

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
  // ★ 换文章时先把上一批配图清掉（避免旧文章的图闪一下），
  //   顺带停掉可能还在跑的批量生成 —— 否则它会继续给**上一篇文章**
  //   一张一张地出图，用户既看不见也停不下来，纯粹在烧钱。
  imageStore.reset()
  lastPublish.value = null

  try {
    const data = await articleStore.fetchDetail(articleId.value)
    await loadAgentRun(data?.agent_run_id ?? null)
    // 详情确实拿到了才去拉配图和推送历史：
    // 文章不存在时那两个接口必然 404，白跑两个请求 + 两条没用的提示
    await Promise.all([loadImages(), loadLastPublish()])
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载文章失败')
  }
}

/** 加载配图列表（走 image store，供配图面板和上面的推送按钮共用） */
async function loadImages(): Promise<void> {
  try {
    await imageStore.fetchImages(articleId.value)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载配图失败')
  }
}

/** 加载最近的推送记录；从来没推过就是 null */
async function loadLastPublish(): Promise<void> {
  try {
    // 只要最新的一条，所以 page_size=1
    const data = await listWeChatPublishes(articleId.value, 1, 1)
    lastPublish.value = data.results[0] ?? null
  } catch {
    // 推送历史拿不到不影响看文章，静默处理
    lastPublish.value = null
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

/** 推送到公众号草稿箱 */
async function handlePush(): Promise<void> {
  if (!article.value || !canPush.value || publishing.value) {
    return
  }

  try {
    await ElMessageBox.confirm(
      pushWillUpdate.value
        ? '会**更新**公众号草稿箱里上次推的那一份草稿（不会新增一份重复的）。'
          + '推送完成后请到公众号后台审核并手动发布。'
        : '会在公众号草稿箱里**新建**一份草稿。'
          + '推送完成后请到公众号后台审核并手动发布。',
      '推送到公众号草稿箱',
      { confirmButtonText: '推送', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    // 用户点了取消，不吞掉会在控制台留一条未处理的报错
    return
  }

  publishing.value = true
  try {
    lastPublish.value = await publishToWeChat(article.value.id)
    // ★ 文案只能是"已推送到草稿箱"，**绝不能**写成"已发布"。
    //   后端从头到尾没有调过发布接口（个人订阅号也没有那个权限），
    //   写成"已发布"会让用户以为文章已经出去了，结果公众号上什么也没有。
    ElMessage.success('已推送到公众号草稿箱，请到公众号后台审核后手动发布')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '推送失败')
    // 失败也要刷一次：后端把失败记录也存进历史了，
    // 页头那句"上次推送"要能反映出刚才这次失败的原因（例如 IP 不在白名单）
    await loadLastPublish()
  } finally {
    publishing.value = false
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
  // 地址栏里的 ?tab=xxx 只认白名单里的值 —— 直接把任意字符串塞给 el-tabs
  // 会让它一个 tab 都不高亮（内容还在，但看着像坏了）。
  const requested = String(route.query.tab ?? '')
  if ((TABS as readonly string[]).includes(requested)) {
    activeTab.value = requested
  }
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
        <!--
          推送到公众号草稿箱。
          ★ 用 el-tooltip 包一个 span 是必须的，不是啰嗦：
            **禁用的 button 不触发鼠标事件**，tooltip 直接挂在它身上永远不会显示。
            包一层 span 让 tooltip 有东西可挂。
        -->
        <el-tooltip :disabled="canPush" :content="pushHint" placement="bottom">
          <span class="push-wrap">
            <el-button
              v-if="article"
              type="success"
              :loading="publishing"
              :disabled="!canPush"
              @click="handlePush"
            >
              推送到公众号草稿箱
            </el-button>
          </span>
        </el-tooltip>

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

    <!--
      上次推送的结果。
      ★ 文案一律只说「草稿」。后端从头到尾没有调过发布接口
        （个人主体订阅号 2025-07 起也没有 freepublish 权限），
        写成「已发布」会让用户以为文章已经出去了，去公众号上却找不到。
    -->
    <div v-if="article && lastPublish" class="publish-line">
      <el-tag size="small" :type="WECHAT_PUBLISH_STATUS_TAG_TYPES[lastPublish.status]">
        {{ lastPublish.status_display }}
      </el-tag>
      <span class="dim">
        上次推送：{{ lastPublish.action_display || '—' }} ·
        {{ formatDateTime(lastPublish.created_at) }} ·
        带 {{ lastPublish.image_count }} 张正文图
      </span>
      <span v-if="lastPublish.error_message" class="publish-error">
        {{ lastPublish.error_message }}
      </span>
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
          <el-tabs v-model="activeTab">
            <!-- 资料来源 -->
            <el-tab-pane label="资料来源" name="sources">
              <SourceList :sources="article.sources" />
            </el-tab-pane>

            <!-- 配图 -->
            <el-tab-pane label="配图" name="images">
              <ArticleImagePanel :article="article" />
            </el-tab-pane>

            <!-- Agent 信息 -->
            <el-tab-pane label="Agent 信息" name="agent">
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
            <el-tab-pane label="风险提示" name="risks">
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

/* tooltip 的挂载点。inline-block 是为了让它跟着按钮的尺寸走 */
.push-wrap {
  display: inline-block;
}

.publish-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: -8px 0 16px;
  font-size: 12px;
}

.publish-error {
  color: #f56c6c;
  word-break: break-all;
}

.dim {
  color: #909399;
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
