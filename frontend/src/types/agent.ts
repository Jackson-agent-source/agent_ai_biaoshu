/**
 * Agent（智能体）相关的数据类型。
 * 对应后端 /agent/... 一组接口。
 */

/** Agent 一次运行的执行状态 */
export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

/**
 * 单次工具调用的状态。
 *
 * blocked = 模型申请调用、但被"研究预算"拦下、**根本没有执行**。
 * 它和 failed 是两回事：failed 是工具跑了但出错，blocked 是压根没跑。
 * 后端单独给它一个状态，前端就必须认得，否则会显示成蓝色的"进行中"，
 * 看起来像卡住了。
 */
export type ToolCallStatus = 'running' | 'success' | 'failed' | 'blocked'

/** 资料来源（搜索结果或网页正文） */
export interface SourceItem {
  id: string
  /** 来源标题 */
  title: string
  /** 原始链接 */
  url: string
  /** 域名，例如 example.com */
  domain: string
  /** 内容摘要片段 */
  snippet: string
  /** 发布时间，可能为空 */
  published_at: string | null
  /** search = 来自搜索接口；webpage = 来自实际抓取的网页 */
  source_type: 'search' | 'webpage'
  /** 这条资料在这次生成中被用来做什么 */
  usage: string
  created_at: string
}

/** 一次工具调用记录（Agent 思考过程中调用了什么工具） */
export interface ToolCall {
  id: string
  /** 工具名，例如 web_search / read_webpage */
  tool_name: string
  /** 调用参数，结构随工具不同而不同 */
  arguments: Record<string, unknown>
  status: ToolCallStatus
  /** 结果预览（截断过的字符串） */
  result_preview: string
  /** 耗时（毫秒），未结束时为 null */
  duration_ms: number | null
  created_at: string
  error_message: string
  /** 这次调用产生的资料来源 */
  sources: SourceItem[]
}

/** 一次 Agent 运行的完整信息 */
export interface AgentRun {
  run_id: string
  status: RunStatus
  model_name: string
  input_text: string
  tool_call_count: number
  error_message: string
  started_at: string | null
  completed_at: string | null
  created_at: string
  /** 这次运行产出的文章 id */
  article_id: string | null
}

/** 会话（一个会话里可以有多次运行） */
export interface Conversation {
  id: string
  title: string
  status: string
  created_at: string
  updated_at: string
  message_count: number
  run_count: number
}

/** 会话里的一条消息 */
export interface Message {
  id: string
  /** 角色，例如 user / assistant / system */
  role: string
  content: string
  message_type: string
  metadata: Record<string, unknown>
  created_at: string
}
