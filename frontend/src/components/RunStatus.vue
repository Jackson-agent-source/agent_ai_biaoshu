<script setup lang="ts">
/**
 * 运行状态标签。
 * 把后端返回的英文状态（pending/running/...）翻译成中文，并用不同颜色区分。
 */

import { computed } from 'vue'

import type { RunStatus } from '@/types/agent'

const props = withDefaults(
  defineProps<{
    /** 运行状态 */
    status: RunStatus
    /** 工具调用次数，可选 */
    toolCallCount?: number
    /** 失败时的错误信息，可选 */
    errorMessage?: string
  }>(),
  {
    toolCallCount: 0,
    errorMessage: '',
  },
)

/** 状态 -> 中文文字 */
const STATUS_LABELS: Record<RunStatus, string> = {
  pending: '等待中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 状态 -> 标签颜色 */
const STATUS_TAG_TYPES: Record<RunStatus, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  pending: 'info',
  running: 'primary',
  completed: 'success',
  failed: 'danger',
  cancelled: 'info',
}

const label = computed(() => STATUS_LABELS[props.status] ?? props.status)
const tagType = computed(() => STATUS_TAG_TYPES[props.status] ?? 'info')
</script>

<template>
  <div class="run-status">
    <el-tag :type="tagType" effect="light">{{ label }}</el-tag>
    <span v-if="toolCallCount > 0" class="run-status-count">
      共调用工具 {{ toolCallCount }} 次
    </span>
    <el-tooltip v-if="status === 'failed' && errorMessage" :content="errorMessage" placement="top">
      <span class="run-status-error">{{ errorMessage }}</span>
    </el-tooltip>
  </div>
</template>

<style scoped>
.run-status {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.run-status-count {
  font-size: 13px;
  color: #909399;
}

.run-status-error {
  font-size: 13px;
  color: #f56c6c;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: help;
}
</style>
