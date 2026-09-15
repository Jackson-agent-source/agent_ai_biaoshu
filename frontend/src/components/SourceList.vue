<script setup lang="ts">
/**
 * 资料来源列表。
 * 展示 Agent 在生成过程中搜到 / 读过的网页。
 */

import type { SourceItem } from '@/types/agent'
import { formatDateTime } from '@/utils/format'

withDefaults(
  defineProps<{
    /** 资料来源列表 */
    sources: SourceItem[]
    /** 是否显示「用途」那一行说明 */
    showUsage?: boolean
  }>(),
  {
    showUsage: true,
  },
)
</script>

<template>
  <div class="source-list">
    <!-- 空状态 -->
    <el-empty v-if="!sources || sources.length === 0" description="本次生成没有使用外部来源" />

    <el-card v-for="source in sources" :key="source.id" class="source-card" shadow="hover">
      <!-- 标题：点击在新标签页打开原始链接 -->
      <a class="source-title" :href="source.url" target="_blank" rel="noopener noreferrer">
        {{ source.title || source.url }}
      </a>

      <div class="source-meta">
        <el-tag size="small" type="info">{{ source.domain }}</el-tag>
        <el-tag size="small" :type="source.source_type === 'webpage' ? 'success' : 'primary'">
          {{ source.source_type === 'webpage' ? '网页正文' : '搜索结果' }}
        </el-tag>
        <span v-if="source.published_at" class="source-published">
          发布于 {{ formatDateTime(source.published_at) }}
        </span>
      </div>

      <!-- 灰色小字显示原始 URL -->
      <div class="source-url">{{ source.url }}</div>

      <div v-if="source.snippet" class="source-snippet">{{ source.snippet }}</div>

      <div v-if="showUsage && source.usage" class="source-usage">用途：{{ source.usage }}</div>
    </el-card>
  </div>
</template>

<style scoped>
.source-card {
  margin-bottom: 12px;
}

.source-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  text-decoration: none;
}

.source-title:hover {
  color: #409eff;
  text-decoration: underline;
}

.source-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.source-published {
  font-size: 12px;
  color: #909399;
}

.source-url {
  margin-top: 6px;
  font-size: 12px;
  color: #a8abb2;
  word-break: break-all;
}

.source-snippet {
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
}

.source-usage {
  margin-top: 8px;
  font-size: 13px;
  color: #e6a23c;
}
</style>
