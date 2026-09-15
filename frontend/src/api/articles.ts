/**
 * 文章相关接口。
 * 对应后端 /articles/... 一组接口。
 */

import { api } from './http'

import type { SourceItem } from '@/types/agent'
import type {
  Article,
  ArticleListItem,
  ArticleListQuery,
  ArticleStats,
  UpdateArticleRequest,
} from '@/types/article'
import type { Paginated } from '@/types/api'

/** 分页查询文章列表，支持按状态筛选和关键词搜索 */
export function listArticles(query: ArticleListQuery = {}): Promise<Paginated<ArticleListItem>> {
  return api.get<Paginated<ArticleListItem>>('/articles', {
    params: {
      page: query.page ?? 1,
      page_size: query.page_size ?? 10,
      // 空字符串不发给后端，避免无效筛选
      ...(query.status ? { status: query.status } : {}),
      ...(query.keyword ? { keyword: query.keyword } : {}),
    },
  })
}

/** 获取仪表盘统计信息（注意后端这个地址带结尾斜杠） */
export function getArticleStats(): Promise<ArticleStats> {
  return api.get<ArticleStats>('/articles/stats/')
}

/** 获取文章详情 */
export function getArticle(id: string): Promise<Article> {
  return api.get<Article>(`/articles/${id}`)
}

/** 局部更新文章（只传要改的字段即可） */
export function updateArticle(id: string, payload: UpdateArticleRequest): Promise<Article> {
  return api.patch<Article>(`/articles/${id}`, payload)
}

/** 删除文章（后端是软删除，会把状态置为 archived），返回 null */
export function deleteArticle(id: string): Promise<null> {
  return api.delete<null>(`/articles/${id}`)
}

/** 获取某篇文章引用的资料来源 */
export function getArticleSources(id: string): Promise<SourceItem[]> {
  return api.get<SourceItem[]>(`/articles/${id}/sources`)
}
