/**
 * 文章相关的数据类型。
 * 对应后端 /articles/... 一组接口。
 */

import type { AgentRun, SourceItem } from './agent'

/** 文章状态：草稿 / 待审 / 已通过 / 已归档 */
export type ArticleStatus = 'draft' | 'review' | 'approved' | 'archived'

/** 状态 -> 中文标签 的映射，多个页面都会用到 */
export const ARTICLE_STATUS_LABELS: Record<ArticleStatus, string> = {
  draft: '草稿',
  review: '待审',
  approved: '已通过',
  archived: '已归档',
}

/** 列表页用的文章数据（字段较少） */
export interface ArticleListItem {
  id: string
  title: string
  subtitle: string
  summary: string
  status: ArticleStatus
  /** 版本号，每次保存 +1 */
  version: number
  model_name: string
  created_at: string
  updated_at: string
}

/** 详情页用的完整文章数据 */
export interface Article extends ArticleListItem {
  /** 大纲，每个元素是一行 */
  outline: string[]
  /** Markdown 正文 */
  content_markdown: string
  /** 后端渲染好的 HTML（前端预览仍然会重新渲染并净化，更安全） */
  content_html: string
  /** 配图建议 */
  image_suggestions: string[]
  /** 风险提示 */
  risk_notes: string[]
  /** 开头钩子 */
  hook: string
  /** 本次文章引用的资料 */
  sources: SourceItem[]
  agent_run_id: string | null
  conversation_id: string | null
  tool_call_count: number
}

/** POST /agent/runs 的请求体（也就是「生成文章」的参数） */
export interface GenerateArticleRequest {
  /** 主题，必填，1..500 字符 */
  topic: string
  /** 目标读者，默认「普通互联网用户」 */
  audience?: string
  /** 写作目标，例如「知识科普」 */
  goal?: string
  /** 语气风格 */
  tone?: string
  /** 篇幅 */
  length?: 'short' | 'medium' | 'long'
  /** 是否联网搜索资料 */
  research_mode?: boolean
  /** 额外要求 */
  extra_requirements?: string
  /** 可选：继续某个已有会话；不传则新建会话 */
  conversation_id?: string
}

/** POST /agent/runs 的返回数据 */
export interface GenerateArticleResponse {
  run_id: string
  article_id: string
  conversation_id: string
  status: string
  model_name: string
  tool_call_count: number
  risk_notes: string[]
}

/** PATCH /articles/{id} 的请求体，所有字段都可选 */
export interface UpdateArticleRequest {
  title?: string
  subtitle?: string
  summary?: string
  content_markdown?: string
  status?: ArticleStatus
  outline?: string[]
  image_suggestions?: string[]
}

/** GET /articles/stats/ 的返回数据 */
export interface ArticleStats {
  total: number
  draft: number
  review: number
  approved: number
  archived: number
  recent_articles: ArticleListItem[]
  recent_runs: AgentRun[]
}

/** 文章列表的查询参数 */
export interface ArticleListQuery {
  page?: number
  page_size?: number
  status?: ArticleStatus | ''
  keyword?: string
}
