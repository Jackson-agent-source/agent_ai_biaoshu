/**
 * Agent 生成过程的仓库。
 *
 * 主要干两件事：
 * 1. generate()  —— 发起一次生成（这个请求很慢，会阻塞几十秒到几分钟）。
 * 2. pollRun()   —— 每 2 秒问一次后端「现在跑到哪一步了」，用来画进度时间线。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import * as agentApi from '@/api/agent'
import type { AgentRun, SourceItem, ToolCall } from '@/types/agent'
import type { GenerateArticleRequest, GenerateArticleResponse } from '@/types/article'

/** 轮询间隔：2 秒 */
const POLL_INTERVAL_MS = 2000

export const useAgentStore = defineStore('agent', () => {
  /** 生成请求是否进行中（用来禁用提交按钮） */
  const loading = ref(false)
  /** 当前这次运行的详情 */
  const currentRun = ref<AgentRun | null>(null)
  /** 当前这次运行的所有工具调用 */
  const toolCalls = ref<ToolCall[]>([])
  /** 从工具调用里汇总出来的资料来源 */
  const sources = ref<SourceItem[]>([])
  /** 最近一次的错误提示 */
  const errorMessage = ref('')
  /** 最后一次 generate 的返回结果（里面有 article_id） */
  const lastResult = ref<GenerateArticleResponse | null>(null)

  /** 定时器句柄；用 ReturnType 兼容浏览器和 Node 两种类型定义 */
  let timer: ReturnType<typeof setInterval> | null = null

  /** 停止轮询 */
  function stopPolling(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 清空上一次的运行数据 */
  function resetRun(): void {
    stopPolling()
    currentRun.value = null
    toolCalls.value = []
    sources.value = []
    errorMessage.value = ''
    lastResult.value = null
  }

  /**
   * 发起一次文章生成。
   * 这个接口是同步的，会一直等到 Agent 跑完才返回，可能要 30~120 秒。
   */
  async function generate(payload: GenerateArticleRequest): Promise<GenerateArticleResponse> {
    loading.value = true
    errorMessage.value = ''
    toolCalls.value = []
    sources.value = []
    currentRun.value = null
    try {
      const result = await agentApi.createRun(payload)
      lastResult.value = result
      return result
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '生成失败，请稍后重试'
      throw error
    } finally {
      loading.value = false
    }
  }

  /** 问一次后端，把当前进度更新到仓库里；返回这次运行是否已经结束 */
  async function refreshOnce(runId: string): Promise<boolean> {
    const [run, calls] = await Promise.all([
      agentApi.getRun(runId),
      agentApi.getToolCalls(runId),
    ])
    currentRun.value = run
    toolCalls.value = calls
    // 每个工具调用自己带着来源，这里汇总成一份总列表
    sources.value = calls.flatMap((call) => call.sources)
    return agentApi.isRunFinished(run.status)
  }

  /**
   * 开始轮询某次运行，直到状态变成 completed / failed / cancelled。
   * 每 2 秒刷新一次工具调用列表。
   */
  async function pollRun(runId: string): Promise<void> {
    stopPolling()
    if (!runId) {
      return
    }

    // 记录是否该停下来（跑完了、或者出错了），避免定时器被重新打开
    let stopped = false

    const tick = async (): Promise<void> => {
      try {
        const finished = await refreshOnce(runId)
        if (finished) {
          stopped = true
          stopPolling()
        }
      } catch (error) {
        // 轮询失败就停下来，避免疯狂刷屏
        stopped = true
        stopPolling()
        errorMessage.value = error instanceof Error ? error.message : '获取运行进度失败'
      }
    }

    await tick()
    // 第一次就已经跑完（或已经出错）的话，就不要再开定时器了
    if (!stopped) {
      timer = setInterval(() => {
        void tick()
      }, POLL_INTERVAL_MS)
    }
  }

  return {
    loading,
    currentRun,
    toolCalls,
    sources,
    errorMessage,
    lastResult,
    generate,
    pollRun,
    stopPolling,
    resetRun,
  }
})
