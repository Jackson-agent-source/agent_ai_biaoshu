<script setup lang="ts">
/**
 * 文章历史：表格 + 关键词搜索 + 状态筛选 + 分页。
 */

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/http'
import { useArticleStore } from '@/stores/article'
import { ARTICLE_STATUS_LABELS, type ArticleListItem, type ArticleStatus } from '@/types/article'
import { formatDateTime, truncate } from '@/utils/format'

const router = useRouter()
const articleStore = useArticleStore()
const { list, total, page, pageSize, listLoading } = storeToRefs(articleStore)

/** 搜索关键词 */
const keyword = ref('')
/** 状态筛选，空字符串表示全部 */
const statusFilter = ref<ArticleStatus | ''>('')

/** 状态选项 */
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

/** 按当前条件加载列表 */
async function load(targetPage: number = page.value): Promise<void> {
  try {
    await articleStore.fetchList({
      page: targetPage,
      page_size: pageSize.value,
      keyword: keyword.value.trim(),
      status: statusFilter.value,
    })
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载文章列表失败')
  }
}

/** 点搜索：回到第一页重新查 */
function handleSearch(): void {
  void load(1)
}

/** 清空条件 */
function handleReset(): void {
  keyword.value = ''
  statusFilter.value = ''
  void load(1)
}

/** 切换页码 */
function handlePageChange(newPage: number): void {
  void load(newPage)
}

/** 切换每页条数 */
function handleSizeChange(newSize: number): void {
  pageSize.value = newSize
  void load(1)
}

/** 打开详情 */
function openDetail(id: string): void {
  void router.push({ name: 'article-detail', params: { id } })
}

/** 点击整行也能进详情 */
function handleRowClick(row: ArticleListItem): void {
  openDetail(row.id)
}

onMounted(() => {
  void load(1)
})
</script>

<template>
  <div class="history-page">
    <div class="page-header">
      <h2 class="page-title">文章历史</h2>
      <el-button type="primary" @click="router.push('/create')">创建新文章</el-button>
    </div>

    <el-card shadow="never">
      <!-- 筛选区 -->
      <div class="filter-bar">
        <el-input
          v-model="keyword"
          placeholder="搜索标题或摘要关键词"
          clearable
          class="filter-keyword"
          @keyup.enter="handleSearch"
        />
        <el-select v-model="statusFilter" placeholder="全部状态" clearable class="filter-status">
          <el-option
            v-for="option in STATUS_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" @click="handleSearch">搜索</el-button>
        <el-button @click="handleReset">重置</el-button>
      </div>

      <!-- 表格 -->
      <el-table
        v-loading="listLoading"
        :data="list"
        empty-text="暂无文章"
        class="article-table"
        @row-click="handleRowClick"
      >
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <div class="cell-title">{{ row.title || '（无标题）' }}</div>
            <div v-if="row.summary" class="cell-summary">{{ truncate(row.summary, 60) }}</div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ ARTICLE_STATUS_LABELS[row.status as ArticleStatus] }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>

        <el-table-column prop="model_name" label="模型" width="150" show-overflow-tooltip />

        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>

        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>

        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <!-- @click.stop 防止触发整行的点击 -->
            <el-button link type="primary" @click.stop="openDetail(row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-bar">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.filter-keyword {
  width: 260px;
}

.filter-status {
  width: 150px;
}

.article-table :deep(.el-table__row) {
  cursor: pointer;
}

.cell-title {
  font-size: 14px;
  color: #303133;
}

.cell-summary {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
