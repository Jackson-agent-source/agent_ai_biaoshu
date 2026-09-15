/**
 * 文章仓库。
 * 管三件事：列表（含分页）、当前详情、以及保存修改。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import * as articlesApi from '@/api/articles'
import type {
  Article,
  ArticleListItem,
  ArticleListQuery,
  ArticleStats,
  UpdateArticleRequest,
} from '@/types/article'

export const useArticleStore = defineStore('article', () => {
  /* --------------------------- 列表相关 --------------------------- */
  const list = ref<ArticleListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(10)
  const listLoading = ref(false)

  /* --------------------------- 详情相关 --------------------------- */
  const current = ref<Article | null>(null)
  const detailLoading = ref(false)
  const saving = ref(false)

  /* --------------------------- 统计相关 --------------------------- */
  const stats = ref<ArticleStats | null>(null)
  const statsLoading = ref(false)

  /** 拉取文章列表 */
  async function fetchList(query: ArticleListQuery = {}): Promise<void> {
    listLoading.value = true
    try {
      const targetPage = query.page ?? page.value
      const targetPageSize = query.page_size ?? pageSize.value
      const data = await articlesApi.listArticles({
        ...query,
        page: targetPage,
        page_size: targetPageSize,
      })
      list.value = data.results
      total.value = data.count
      page.value = data.page
      pageSize.value = data.page_size
    } finally {
      listLoading.value = false
    }
  }

  /** 拉取文章详情 */
  async function fetchDetail(id: string): Promise<Article | null> {
    detailLoading.value = true
    current.value = null
    try {
      const data = await articlesApi.getArticle(id)
      current.value = data
      return data
    } finally {
      detailLoading.value = false
    }
  }

  /** 保存文章修改，成功后同步更新 current */
  async function update(id: string, payload: UpdateArticleRequest): Promise<Article> {
    saving.value = true
    try {
      const data = await articlesApi.updateArticle(id, payload)
      current.value = data
      return data
    } finally {
      saving.value = false
    }
  }

  /** 拉取仪表盘统计 */
  async function fetchStats(): Promise<ArticleStats> {
    statsLoading.value = true
    try {
      const data = await articlesApi.getArticleStats()
      stats.value = data
      return data
    } finally {
      statsLoading.value = false
    }
  }

  /** 删除（后端是软删除，会置为 archived） */
  async function remove(id: string): Promise<void> {
    await articlesApi.deleteArticle(id)
  }

  return {
    list,
    total,
    page,
    pageSize,
    listLoading,
    current,
    detailLoading,
    saving,
    stats,
    statsLoading,
    fetchList,
    fetchDetail,
    update,
    fetchStats,
    remove,
  }
})
