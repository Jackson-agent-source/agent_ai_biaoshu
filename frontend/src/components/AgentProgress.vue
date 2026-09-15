<script setup lang="ts">
/**
 * Agent 执行进度时间线。
 *
 * 用法：<AgentProgress :run-id="runId" :active="loading" />
 *
 * 它会每 2 秒去问一次后端「现在跑到哪一步了」，然后画成一条时间线：
 *   搜索资料 -> 阅读网页 -> ... -> 生成文章 -> 保存草稿
 */

import { computed, onBeforeUnmount, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Loading } from '@element-plus/icons-vue'

import { useAgentStore } from '@/stores/agent'
import type { ToolCallStatus } from '@/types/agent'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  /** 运行 id */
  runId: string
  /** 是否正在生成中；为 true 时才会轮询 */
  active: boolean
}>()

const agentStore = useAgentStore()
const { toolCalls, currentRun, errorMessage } = storeToRefs(agentStore)

/** 工具名 -> 中文说明 */
const TOOL_LABELS: Record<string, string> = {
  web_search: '搜索资料',
  read_webpage: '阅读网页',
}

/** 时间线上的一条步骤 */
interface Step {
  key: string
  /** 中文标题 */
  title: string
  /** 次要说明，比如搜索的关键词、网页地址 */
  detail: string
  /** 状态，决定时间线节点的颜色 */
  status: ToolCallStatus
  /** 耗时（毫秒） */
  durationMs: number | null
  /** 出错信息 */
  errorMessage: string
  /** 这一步产生了多少条资料 */
  sourceCount: number
  /** 发生时间 */
  createdAt: string
}

/** 从参数对象里安全地取一个字符串字段 */
function readStringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key]
  return typeof value === 'string' ? value : ''
}

/** 把工具调用转换成时间线步骤 */
const steps = computed<Step[]>(() => {
  const list: Step[] = toolCalls.value.map((call) => {
    // 不同工具展示的说明不一样
    let detail = ''
    if (call.tool_name === 'web_search') {
      const query = readStringArg(call.arguments, 'query')
      detail = query ? `搜索关键词：${query}` : ''
    } else if (call.tool_name === 'read_webpage') {
      const url = readStringArg(call.arguments, 'url')
      detail = url ? `网页地址：${url}` : ''
    }
    if (!detail && call.result_preview) {
      detail = call.result_preview
    }

    return {
      key: call.id,
      title: TOOL_LABELS[call.tool_name] ?? call.tool_name,
      detail,
      status: call.status,
      durationMs: call.duration_ms,
      errorMessage: call.error_message,
      sourceCount: call.sources.length,
      createdAt: call.created_at,
    }
  })

  // 运行成功结束后，补上最后两步，让整条流程看起来完整
  if (currentRun.value?.status === 'completed') {
    list.push({
      key: '__generate__',
      title: '生成文章',
      detail: `使用模型 ${currentRun.value.model_name}`,
      status: 'success',
      durationMs: null,
      errorMessage: '',
      sourceCount: 0,
      createdAt: currentRun.value.completed_at ?? currentRun.value.created_at,
    })
    list.push({
      key: '__save__',
      title: '保存草稿',
      detail: '已写入文章库，可以在「文章历史」里查看',
      status: 'success',
      durationMs: null,
      errorMessage: '',
      sourceCount: 0,
      createdAt: currentRun.value.completed_at ?? currentRun.value.created_at,
    })
  }

  return list
})

/** 时间线节点颜色：成功=绿，失败=红，被预算拦下=橙，进行中=蓝 */
function timelineType(status: ToolCallStatus): 'success' | 'danger' | 'warning' | 'primary' {
  if (status === 'success') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  // 被预算拦下用橙色而不是红色：不是出错，是正常的研究上限控制，
  // 用红色会让人以为工具坏了。
  if (status === 'blocked') {
    return 'warning'
  }
  return 'primary'
}

/** 状态中文 */
function statusText(status: ToolCallStatus): string {
  if (status === 'success') {
    return '完成'
  }
  if (status === 'failed') {
    return '失败'
  }
  if (status === 'blocked') {
    return '已跳过'
  }
  return '进行中'
}

/** 标签颜色，和时间线节点保持一致 */
function statusTagType(status: ToolCallStatus): 'success' | 'danger' | 'warning' | 'primary' {
  return timelineType(status)
}

/** 耗时显示，例如 1.2 秒 */
function durationText(ms: number | null): string {
  if (ms === null || ms === undefined) {
    return ''
  }
  return `${(ms / 1000).toFixed(1)} 秒`
}

/** 运行是否已经结束 */
const isFinished = computed(() => {
  const status = currentRun.value?.status
  return status === 'completed' || status === 'failed' || status === 'cancelled'
})

// 监听 runId / active 变化：需要轮询就开，不需要就停
watch(
  () => [props.runId, props.active] as const,
  ([runId, active]) => {
    if (active && runId) {
      void agentStore.pollRun(runId)
    } else {
      agentStore.stopPolling()
    }
  },
  { immediate: true },
)

// 组件销毁时一定要停掉定时器，否则会一直请求后端
onBeforeUnmount(() => {
  agentStore.stopPolling()
})
</script>

<template>
  <el-card class="agent-progress" shadow="never">
    <template #header>
      <div class="progress-header">
        <span class="progress-title">Agent 执行进度</span>
        <span v-if="props.active" class="progress-hint">
          <el-icon class="is-loading"><Loading /></el-icon>
          正在生成中，请勿关闭页面……
        </span>
        <span v-else-if="isFinished" class="progress-hint">已结束</span>
      </div>
    </template>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
      class="progress-alert"
    />

    <!-- 还没有任何工具调用，且正在跑 -->
    <el-skeleton v-if="steps.length === 0 && props.active" :rows="3" animated />

    <el-empty
      v-else-if="steps.length === 0"
      description="暂无执行记录"
      :image-size="70"
    />

    <el-timeline v-else>
      <el-timeline-item
        v-for="step in steps"
        :key="step.key"
        :type="timelineType(step.status)"
        :hollow="step.status === 'running'"
        :timestamp="formatDateTime(step.createdAt)"
        placement="top"
      >
        <div class="step-title">
          {{ step.title }}
          <el-tag size="small" :type="statusTagType(step.status)">
            {{ statusText(step.status) }}
          </el-tag>
          <span v-if="durationText(step.durationMs)" class="step-duration">
            {{ durationText(step.durationMs) }}
          </span>
        </div>

        <div v-if="step.detail" class="step-detail">{{ step.detail }}</div>

        <div v-if="step.sourceCount > 0" class="step-sources">
          获得 {{ step.sourceCount }} 条资料来源
        </div>

        <div v-if="step.errorMessage" class="step-error">{{ step.errorMessage }}</div>
      </el-timeline-item>
    </el-timeline>
  </el-card>
</template>

<style scoped>
.agent-progress {
  margin-top: 16px;
}

.progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.progress-title {
  font-weight: 600;
  color: #303133;
}

.progress-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #909399;
}

.progress-alert {
  margin-bottom: 12px;
}

.step-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.step-duration {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}

.step-detail {
  margin-top: 4px;
  font-size: 13px;
  color: #606266;
  word-break: break-all;
}

.step-sources {
  margin-top: 4px;
  font-size: 13px;
  color: #67c23a;
}

.step-error {
  margin-top: 4px;
  font-size: 13px;
  color: #f56c6c;
}
</style>
