/**
 * Agent 相关接口。
 * 对应后端 /agent/... 一组接口。
 */

import { api } from './http'

import type { AgentRun, Conversation, Message, RunStatus, SourceItem, ToolCall } from '@/types/agent'
import type { GenerateArticleRequest, GenerateArticleResponse } from '@/types/article'
import type { Paginated } from '@/types/api'

/**
 * Agent 生成接口的专用超时时间（5 分钟）。
 * 这个接口是「同步」的：后端会一直等到 Agent 跑完（可能 30~120 秒）才返回，
 * 所以必须给它单独放宽 timeout，否则会被默认的 30 秒掐断。
 */
export const AGENT_GENERATE_TIMEOUT = 300000

/**
 * 发起一次文章生成。
 * 注意：这个请求会阻塞很久，界面上要显示「生成中」并禁用重复提交。
 */
export function createRun(payload: GenerateArticleRequest): Promise<GenerateArticleResponse> {
  return api.post<GenerateArticleResponse>('/agent/runs', payload, {
    timeout: AGENT_GENERATE_TIMEOUT,
  })
}

/** 查询某次运行的详情（状态、模型名、工具调用次数等） */
export function getRun(runId: string): Promise<AgentRun> {
  return api.get<AgentRun>(`/agent/runs/${runId}`)
}

/** 查询某次运行过程中的所有工具调用（用来画进度时间线） */
export function getToolCalls(runId: string): Promise<ToolCall[]> {
  return api.get<ToolCall[]>(`/agent/runs/${runId}/tool-calls`)
}

/** 查询某次运行用到的资料来源 */
export function getRunSources(runId: string): Promise<SourceItem[]> {
  return api.get<SourceItem[]>(`/agent/runs/${runId}/sources`)
}

/** 分页查询运行历史 */
export function listRuns(page = 1, pageSize = 10): Promise<Paginated<AgentRun>> {
  return api.get<Paginated<AgentRun>>('/agent/runs', {
    params: { page, page_size: pageSize },
  })
}

/** 分页查询会话列表 */
export function listConversations(page = 1, pageSize = 10): Promise<Paginated<Conversation>> {
  return api.get<Paginated<Conversation>>('/agent/conversations', {
    params: { page, page_size: pageSize },
  })
}

/** 查询某个会话下的所有消息 */
export function getConversationMessages(conversationId: string): Promise<Message[]> {
  return api.get<Message[]>(`/agent/conversations/${conversationId}/messages`)
}

/** 判断一个运行状态是否已经「结束」（结束就不用再轮询了） */
export function isRunFinished(status: RunStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled'
}
