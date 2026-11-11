<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { getTrendOverview, refreshTrends, generateFromTopic, saveTrendPreferences, restoreTopic } from '@/api/trends'
import { ApiError } from '@/api/http'
import type { TopicCandidate, TrendOverview } from '@/types/trends'

const router = useRouter()
const loading = ref(false)
const refreshing = ref(false)
const saving = ref(false)
const generatingId = ref('')
const restoringId = ref('')
const overview = ref<TrendOverview>({ candidates: [], sources: [], preferences: { focus_terms: [], auto_categories: [] }, automation: { enabled: false, daily_article_limit: 0 }, tracking: { active: false, state: 'not_running' }, max_focus_terms: 16, research_presets: [], categories: [], source_catalog: [] })
const focusInput = ref('')
const autoCategories = ref<string[]>([])
const selectedCategory = ref('focus')
const selectedResearch = ref('all')
const inputTerms = computed(() => [...new Set(focusInput.value.split(/[,，\n|｜]+/).map(value => value.trim()).filter(Boolean))])
const sourceNames = computed<Record<string, string>>(() => Object.fromEntries(overview.value.source_catalog.map(item => [item.key, item.label])))
const sourceRuns = computed(() => Object.fromEntries(overview.value.sources.map(item => [item.platform, item])))
const statusNames: Record<string, string> = { ready: '待生成', generating: '生成中', generated: '已生成', failed: '失败', skipped: '已暂存' }
const categoryNames = computed(() => Object.fromEntries(overview.value.categories.map(item => [item.key, item.label])))
const visibleCandidates = computed(() => overview.value.candidates.filter(item =>
  (selectedCategory.value === 'all' || item.category === selectedCategory.value)
  && (selectedResearch.value === 'all' || item.trends.some(trend => trend.research_topic === selectedResearch.value))))
const categoryCount = (key: string): number => overview.value.candidates.filter(item => item.category === key).length
const researchCount = (label: string): number => overview.value.candidates.filter(item => item.trends.some(trend => trend.research_topic === label)).length

function toggleResearch(label: string): void {
  const terms = inputTerms.value
  if (terms.includes(label)) {
    focusInput.value = terms.filter(term => term !== label).join('，')
  } else if (terms.length < overview.value.max_focus_terms) {
    focusInput.value = [...terms, label].join('，')
  } else {
    ElMessage.warning(`最多选择 ${overview.value.max_focus_terms} 个研究方向`)
  }
}
const automationText = computed(() => {
  if (!overview.value.automation.enabled || overview.value.automation.daily_article_limit < 1) {
    return '自动生成已在服务配置中关闭；仍可采集选题和手动生成草稿。'
  }
  return `每天每个账号最多自动生成 ${overview.value.automation.daily_article_limit} 篇；仅对已运行的定时任务生效。`
})

function sourceTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : '请求失败，请稍后重试'
}

async function load(): Promise<boolean> {
  loading.value = true
  try {
    overview.value = await getTrendOverview()
    focusInput.value = overview.value.preferences.focus_terms.join('，')
    autoCategories.value = [...overview.value.preferences.auto_categories]
    if (!overview.value.preferences.focus_terms.includes(selectedResearch.value)) selectedResearch.value = 'all'
    return true
  } catch (error) {
    ElMessage.error(errorText(error))
    return false
  } finally {
    loading.value = false
  }
}

async function savePreferences(): Promise<void> {
  const terms = inputTerms.value
  if (!terms.length) {
    ElMessage.warning('请至少填写一个关注词')
    return
  }
  saving.value = true
  try {
    await saveTrendPreferences({ focus_terms: terms, auto_categories: autoCategories.value })
    // 服务端会跳过旧关注方向的候选，必须同步刷新其状态。
    if (await load()) {
      ElMessage.success('设置已保存；点击“采集最新热点”获取新方向的内容')
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function refresh(): Promise<void> {
  refreshing.value = true
  try {
    overview.value = await refreshTrends()
    ElMessage.success(`已更新，获得 ${overview.value.candidates.length} 个候选选题`)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    refreshing.value = false
  }
}

async function generate(candidate: TopicCandidate): Promise<void> {
  generatingId.value = candidate.id
  try {
    const result = await generateFromTopic(candidate.id)
    ElMessage.success('文章草稿已生成')
    await router.push({ name: 'article-detail', params: { id: result.article_id } })
  } catch (error) {
    ElMessage.error(errorText(error))
    await load()
  } finally {
    generatingId.value = ''
  }
}

async function restore(candidate: TopicCandidate): Promise<void> {
  restoringId.value = candidate.id
  try {
    const restored = await restoreTopic(candidate.id)
    const index = overview.value.candidates.findIndex(item => item.id === candidate.id)
    if (index >= 0) overview.value.candidates[index] = restored
    ElMessage.success('选题已恢复，可以继续研究并生成草稿')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    restoringId.value = ''
  }
}

let pollingTimer: ReturnType<typeof setInterval> | undefined
let polling = false
async function pollOverview(): Promise<void> {
  if (polling || loading.value || saving.value || refreshing.value || generatingId.value || restoringId.value) return
  polling = true
  try {
    const fresh = await getTrendOverview()
    // 后台更新列表，不覆盖用户正在编辑的关注词或类别。
    overview.value.candidates = fresh.candidates
    overview.value.sources = fresh.sources
    overview.value.tracking = fresh.tracking
  } catch {
    // 短暂断网保留上次结果；下一次轮询自动重试。
  } finally {
    polling = false
  }
}
onMounted(() => { void load(); pollingTimer = setInterval(() => { void pollOverview() }, 60000) })
onUnmounted(() => { if (pollingTimer) clearInterval(pollingTimer) })
</script>

<template>
  <div class="trend-page" v-loading="loading">
    <div class="page-head">
      <div>
        <h2>个人研究与热点选题</h2>
        <p>按研究方向分别采集资讯，积累工具评测、企业案例与市场研究线索。</p>
      </div>
      <el-button type="primary" :loading="refreshing" :disabled="!!generatingId || saving" @click="refresh">
        {{ refreshing ? '采集中…' : '采集最新热点' }}
      </el-button>
    </div>

    <el-alert :type="overview.tracking.active ? 'success' : 'info'" :closable="false" class="tracking-status">
      <template #title>{{ overview.tracking.active ? (overview.tracking.state === 'collecting' ? '正在追踪采集' : '定时追踪运行中') : '本地定时追踪未运行' }}</template>
      <template v-if="overview.tracking.active">
        每轮完成后间隔 {{ Math.round((overview.tracking.interval_seconds || 3600) / 60) }} 分钟；仅更新资讯和选题。
        <span v-if="overview.tracking.next_run_at">下次采集：{{ sourceTime(overview.tracking.next_run_at) }}。</span>
        <span v-if="overview.tracking.last_success_at">最近成功：{{ sourceTime(overview.tracking.last_success_at) }}。</span>
        <span v-if="overview.tracking.message">{{ overview.tracking.message }}</span>
      </template>
      <template v-else>需要保持电脑和追踪进程运行；本页面每分钟自动检查最新采集结果。</template>
    </el-alert>
    <el-card shadow="never" class="preferences-card">
      <template #header>我的研究方向与自动草稿</template>
      <div class="research-presets" v-for="group in ['主线', '拓展']" :key="group">
        <strong>{{ group === '主线' ? '核心研究方向' : '相关主题拓展' }}</strong>
        <div class="research-options">
          <el-button v-for="preset in overview.research_presets.filter(item => item.group === group)" :key="preset.label"
            :type="inputTerms.includes(preset.label) ? 'primary' : 'default'" plain
            :title="preset.description" :disabled="saving || refreshing || !!generatingId" @click="toggleResearch(preset.label)">
            {{ preset.label }}
          </el-button>
        </div>
        <p class="muted" v-for="preset in overview.research_presets.filter(item => item.group === group && inputTerms.includes(item.label))" :key="preset.label">
          {{ preset.label }}：{{ preset.description }}
        </p>
      </div>
      <div class="preference-row">
        <label for="focus-terms">关注词</label>
        <el-input id="focus-terms" v-model="focusInput" placeholder="例如：AI工具研究，AI落地企业，美股，BTC，港卡" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
        <el-button type="primary" plain :loading="saving" :disabled="refreshing || !!generatingId" @click="savePreferences">保存设置</el-button>
      </div>
      <p class="muted">点击上方主题可添加或移除，保存后生效；也可用逗号或竖线填写自定义主题，最多 {{ overview.max_focus_terms }} 个。各方向分别检索近 7 天资讯，再合并去重。</p>
      <div class="auto-row">
        <strong>定时生成草稿的类别</strong>
        <el-checkbox-group v-model="autoCategories">
          <el-checkbox v-for="category in overview.categories" :key="category.key" :label="category.key">{{ category.key === 'focus' ? '我的研究方向' : category.label }}</el-checkbox>
        </el-checkbox-group>
      </div>
      <p class="muted">留空则只采集和推荐，所有类别仍可手动生成。{{ automationText }}</p>
    </el-card>

    <el-card shadow="never" class="source-card">
      <template #header>数据来源</template>
      <div class="source-list">
        <div v-for="source in overview.source_catalog" :key="source.key" class="source-row">
          <strong>{{ source.label }}</strong>
          <template v-if="sourceRuns[source.key]">
            <el-tag :type="sourceRuns[source.key].status === 'success' ? 'success' : sourceRuns[source.key].status === 'skipped' ? 'info' : 'danger'">
              {{ sourceRuns[source.key].status === 'success' ? '采集成功' : sourceRuns[source.key].status === 'skipped' ? '未配置' : '采集失败' }}
            </el-tag>
            <span>{{ sourceRuns[source.key].item_count }} 条</span>
            <span class="muted">采集于 {{ sourceTime(sourceRuns[source.key].created_at) }}</span>
            <span class="muted">{{ sourceRuns[source.key].message }}</span>
          </template>
          <el-tag v-else type="info">尚未采集</el-tag>
        </div>
      </div>
    </el-card>

    <div class="research-filter">
      <span>研究方向筛选：</span>
      <el-radio-group v-model="selectedResearch">
        <el-radio-button value="all">全部方向</el-radio-button>
        <el-radio-button v-for="label in overview.preferences.focus_terms" :key="label" :value="label">{{ label }} {{ researchCount(label) }}</el-radio-button>
      </el-radio-group>
    </div>
    <div class="category-filter">
      <el-radio-group v-model="selectedCategory">
        <el-radio-button value="all">全部 {{ overview.candidates.length }}</el-radio-button>
        <el-radio-button v-for="category in overview.categories" :key="category.key" :value="category.key">{{ category.key === 'focus' ? '关注方向' : category.label }} {{ categoryCount(category.key) }}</el-radio-button>
      </el-radio-group>
    </div>
    <el-empty v-if="!visibleCandidates.length && !loading" description="该分类暂无选题，点击“采集最新热点”更新" />
    <el-card v-for="candidate in visibleCandidates" :key="candidate.id" shadow="never" class="topic-card">
      <div class="topic-head">
        <h3>{{ candidate.topic }}</h3>
        <div class="topic-tags">
          <el-tag v-for="label in [...new Set(candidate.trends.map(item => item.research_topic).filter(Boolean))]" :key="label" type="warning" effect="plain">{{ label }}</el-tag>
          <el-tag effect="plain">{{ categoryNames[candidate.category] || '其他' }}</el-tag>
          <el-tag :type="candidate.status === 'generated' ? 'success' : candidate.status === 'failed' ? 'danger' : 'info'">{{ statusNames[candidate.status] }}</el-tag>
        </div>
      </div>
      <p v-if="candidate.angle"><strong>切入角度：</strong>{{ candidate.angle }}</p>
      <p v-if="candidate.reason"><strong>推荐理由：</strong>{{ candidate.reason }}</p>
      <p v-if="candidate.error_message" class="error">{{ candidate.error_message }}</p>
      <div class="source-links">
        <span>热点来源：</span>
        <a v-for="trend in candidate.trends" :key="trend.id" :href="trend.url" target="_blank" rel="noopener noreferrer">
          {{ sourceNames[trend.platform] || trend.platform }} · {{ trend.title }}
        </a>
      </div>
      <div class="topic-actions">
        <span class="muted">参考分 {{ candidate.score }}</span>
        <el-button v-if="candidate.article_id" type="primary" link @click="router.push({ name: 'article-detail', params: { id: candidate.article_id } })">查看文章</el-button>
        <template v-else-if="candidate.status === 'skipped'">
          <span class="muted">旧方向选题已暂存，仍感兴趣可恢复</span>
          <el-button type="primary" plain :loading="restoringId === candidate.id"
            :disabled="!!generatingId || refreshing || saving || (!!restoringId && restoringId !== candidate.id)"
            @click="restore(candidate)">恢复选题</el-button>
        </template>
        <el-button v-else type="primary" :loading="generatingId === candidate.id" :disabled="candidate.status === 'generating' || refreshing || saving || (!!generatingId && generatingId !== candidate.id)" @click="generate(candidate)">
          {{ candidate.status === 'failed' ? '重试生成' : '生成文章草稿' }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.trend-page { max-width: 1100px; margin: 0 auto; }
.tracking-status { margin-bottom: 18px; }
.research-presets { margin-bottom: 18px; }
.research-options { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.research-options .el-button { margin-left: 0; }
.research-filter { margin-bottom: 16px; }
.research-filter .el-radio-group { display: flex; flex-wrap: wrap; margin-top: 8px; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.page-head h2 { margin: 0 0 6px; }
.page-head p, .muted { color: #909399; }
.page-head p { margin: 0; }
.source-card, .topic-card { margin-bottom: 16px; }
.preferences-card { margin-bottom: 16px; }
.preference-row { display: flex; align-items: center; gap: 12px; }
.preference-row label { flex: none; font-weight: 600; }
.preference-row .el-input { max-width: 640px; }
.preferences-card .muted { margin: 8px 0 0; font-size: 13px; }
.auto-row { margin-top: 18px; display: flex; align-items: flex-start; gap: 18px; flex-wrap: wrap; }
.auto-row strong { flex: none; padding-top: 3px; }
.auto-row .el-checkbox-group { display: flex; flex-wrap: wrap; gap: 0 16px; }
.category-filter { margin: 20px 0 16px; overflow-x: auto; }
.source-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.source-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.topic-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.topic-head h3 { margin: 0; font-size: 18px; }
.topic-tags { display: flex; gap: 8px; flex: none; }
.topic-card p { color: #606266; line-height: 1.6; }
.source-links { display: flex; flex-wrap: wrap; gap: 8px; color: #606266; }
.source-links a { color: #409eff; text-decoration: none; }
.source-links a:hover { text-decoration: underline; }
.topic-actions { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 18px; }
.error { color: #f56c6c !important; }
@media (max-width: 700px) { .page-head { align-items: flex-start; flex-direction: column; } .source-list { grid-template-columns: 1fr; } .preference-row { align-items: stretch; flex-direction: column; } .topic-head { align-items: flex-start; flex-direction: column; } }
</style>
