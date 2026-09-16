/**
 * 配图仓库。
 *
 * 管四件事：当前文章的配图列表、单张生成、批量生成、上传实拍图。
 *
 * ★ 批量生成为什么是**串行**的（一张跑完再跑下一张）：
 *   后端一次只出一张图，一张要 30~90 秒。
 *   并发发出去只会让几个请求一起在服务端排队，谁先回来不确定，
 *   进度没法画，而且一张失败会连累其他几张的提示。
 *   串行 + 每张回来就插进列表，用户能立刻看到第一张图，
 *   而不是盯着一个转圈等十分钟。
 *
 * ★ 为什么不用轮询（不像 stores/agent.ts 那样每 2 秒问一次）：
 *   生成接口本身就是同步的，HTTP 响应回来就等于"这张图出完了"，
 *   没有任何中间状态可问。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as imagesApi from '@/api/images'
import { shrinkImageForUpload } from '@/utils/imageResize'
import type { GeneratedImage, GenerateImageRequest, UpdateImageRequest } from '@/types/image'

/** 批量生成的进度。不在批量生成时 total 为 0 */
export interface BatchProgress {
  /** 已经处理完几张（含失败的） */
  done: number
  /** 这批一共要处理几张 */
  total: number
  /** 正在生成的是第几条配图建议；-1 表示当前这张不属于任何建议 */
  currentIndex: number
}

/**
 * 和后端 GeneratedImage.Meta.ordering 保持一致的排序：
 * anchor_index → sort_order → created_at 倒序。
 *
 * ★ 前端自己排一遍，是为了批量生成时不用每张图都重新拉一次列表 ——
 *   那会让面板的 loading 转圈闪 8 次，也会多 8 个请求。
 *   ★ 顺序必须和后端一致，否则"生成一张图 → 界面上的位置和刷新后不一样"。
 */
function sortImages(list: GeneratedImage[]): GeneratedImage[] {
  return [...list].sort((a, b) => {
    if (a.anchor_index !== b.anchor_index) {
      return a.anchor_index - b.anchor_index
    }
    if (a.sort_order !== b.sort_order) {
      return a.sort_order - b.sort_order
    }
    if (a.created_at === b.created_at) {
      return 0
    }
    // created_at 是 DRF 给的 ISO 字符串（都是 UTC 带 Z），
    // 所以字典序就是时间序，不用 new Date 解析。
    return a.created_at < b.created_at ? 1 : -1
  })
}

export const useImageStore = defineStore('image', () => {
  /** 当前文章的配图（已排好序） */
  const images = ref<GeneratedImage[]>([])
  /** 正文里的小节数，用来渲染"插入位置"下拉框 */
  const sectionCount = ref(0)
  /** 列表加载中 */
  const listLoading = ref(false)
  /** 有图正在生成（单张或批量都算），用来禁用按钮 */
  const generating = ref(false)
  /**
   * 有实拍图正在上传，用来禁用按钮。
   *
   * ★ 和 generating 分开两个标志、而不是合成一个"busy"：
   *   界面上的说法完全不一样（"正在生成第 N 条建议的配图" vs
   *   "正在上传第 N 张实拍图"），而且两者可以各自独立地被触发。
   *   合成一个的话，改提示语时就得靠猜"现在到底是哪种"。
   */
  const uploading = ref(false)
  /** 批量进度 */
  const progress = ref<BatchProgress>({ done: 0, total: 0, currentIndex: -1 })
  /** 最近一次批量生成里第一条错误（用于统一提示） */
  const errorMessage = ref('')

  /**
   * 批量生成的"请求停止"标志。
   *
   * ★ 和 stores/agent.ts 里那个模块级 timer 是一个套路：
   *   它**不需要**是响应式的 —— 界面靠 generating / progress 的变化
   *   就能反映出来，放进 ref 只会让每次赋值都多触发一轮渲染。
   *   放在 store 的闭包里而不是组件里，是因为组件卸载后循环还在跑，
   *   那时候组件里的变量已经没人看了。
   */
  let stopRequested = false

  /** 生成成功的图（推送公众号至少要有一张，按钮据此启用） */
  const succeededImages = computed(() =>
    images.value.filter((item) => item.status === 'succeeded' && item.file_url),
  )

  /** 已经有图的建议序号（批量生成时跳过它们，避免同一条建议重复出图） */
  const usedSuggestionIndexes = computed(
    () => new Set(images.value.map((item) => item.suggestion_index)),
  )

  /** 拉取配图列表 */
  async function fetchImages(articleId: string): Promise<void> {
    listLoading.value = true
    try {
      const data = await imagesApi.listArticleImages(articleId)
      images.value = sortImages(data.results)
      sectionCount.value = data.section_count
    } finally {
      listLoading.value = false
    }
  }

  /** 生成一张，成功后插进列表并返回。失败时抛出，由调用方决定怎么提示 */
  async function generateOne(
    articleId: string,
    payload: GenerateImageRequest,
  ): Promise<GeneratedImage> {
    const image = await imagesApi.generateImage(articleId, payload)
    images.value = sortImages([...images.value, image])
    return image
  }

  /** 上传一张，成功后插进列表并返回。失败时抛出，由调用方决定怎么提示 */
  async function uploadOne(articleId: string, file: File): Promise<GeneratedImage> {
    const image = await imagesApi.uploadArticleImage(articleId, file)
    images.value = sortImages([...images.value, image])
    return image
  }

  /**
   * 上传若干张实拍图（**串行**）。
   *
   * @param files 用户选中的原始文件（压缩在循环里逐张做，见下）
   * @returns 成功的张数
   *
   * ★ 为什么也是串行：和批量生成同一个理由，但这里更实在 ——
   *   并发传几张，几张平分本来就紧张的上行带宽（服务器在美国），
   *   每张都变慢，而且进度条会同时跳几下，看不出在干什么。
   *   串行传，用户能一张一张看着图出现。
   *
   * ★ 压缩放在循环里**逐张做**而不是先全部压完再传：
   *   一次选十张照片，十张全尺寸位图同时解在内存里，
   *   手机上很容易把标签页搞崩（canvas 那块内存不受 JS 堆限制）。
   *   一张压完、传完、丢掉，峰值只有一张。
   *
   * ★ 单张失败**不中断整批**（和批量生成一致）：选十张照片，
   *   其中一张是 HEIC 之类浏览器解不开的格式，不该让另外九张也传不上去。
   *
   * ★ 用户点「停止」时用**同一个** stopRequested 标志（见 stopBatch）——
   *   当前这张会传完（请求已经发出去了，取消不了），后面的不再开始。
   */
  async function uploadBatch(articleId: string, files: File[]): Promise<number> {
    if (files.length === 0) {
      return 0
    }

    stopRequested = false
    uploading.value = true
    errorMessage.value = ''
    // currentIndex 固定 -1：那个字段是"第几条配图建议"，
    // 而上传的图不属于任何一条建议（后端也不会给它记 suggestion_index）。
    progress.value = { done: 0, total: files.length, currentIndex: -1 }

    let succeeded = 0
    let firstError = ''

    try {
      for (const file of files) {
        if (stopRequested) {
          break
        }
        try {
          const shrunk = await shrinkImageForUpload(file)
          await uploadOne(articleId, shrunk)
          succeeded += 1
        } catch (error) {
          if (!firstError) {
            firstError = error instanceof Error ? error.message : '上传失败'
          }
        } finally {
          progress.value = { ...progress.value, done: progress.value.done + 1 }
        }
      }
    } finally {
      uploading.value = false
      progress.value = { done: 0, total: 0, currentIndex: -1 }
    }

    errorMessage.value = firstError
    return succeeded
  }

  /**
   * 请求停止批量生成**或批量上传**。
   *
   * ★ 两者共用同一个标志是刻意的：它们不可能同时在跑
   *   （界面上的按钮互相禁用），所以不需要两个标志；
   *   而分成两个的话，将来一定会有人只重置了其中一个。
   *
   * 当前这一张会跑完 —— 请求已经发出去了，前端取消不了。
   */
  function stopBatch(): void {
    stopRequested = true
  }
  /**
   * 按配图建议批量生成（串行）。
   *
   * @param articleId 文章 id
   * @param suggestions 配图建议原文列表
   * @returns 成功的张数
   *
   * ★ 已经有图的建议会跳过 —— 用户点"生成剩余 3 张"时，
   *   不该把已经有图的那几条重做一遍（那是白花钱）。
   * ★ 单张失败**不中断整批**：常见原因是这一条建议触发了内容审核，
   *   后面几条完全可能是好的。记下第一条错误，最后统一提示。
   */
  async function generateBatch(articleId: string, suggestions: string[]): Promise<number> {
    const used = usedSuggestionIndexes.value
    const pending = suggestions
      .map((text, index) => ({ text, index }))
      .filter((item) => !used.has(item.index))

    if (pending.length === 0) {
      return 0
    }

    stopRequested = false
    generating.value = true
    errorMessage.value = ''
    progress.value = { done: 0, total: pending.length, currentIndex: -1 }

    let succeeded = 0
    let firstError = ''

    try {
      for (const item of pending) {
        if (stopRequested) {
          break
        }
        progress.value = { ...progress.value, currentIndex: item.index }
        try {
          await generateOne(articleId, {
            suggestion_index: item.index,
            suggestion: item.text,
          })
          succeeded += 1
        } catch (error) {
          if (!firstError) {
            firstError = error instanceof Error ? error.message : '生成失败'
          }
        } finally {
          progress.value = { ...progress.value, done: progress.value.done + 1 }
        }
      }
    } finally {
      generating.value = false
      progress.value = { done: 0, total: 0, currentIndex: -1 }
    }

    errorMessage.value = firstError
    return succeeded
  }

  /** 改图注 / 插入位置 / 顺序 / 是否封面 */
  async function updateOne(imageId: string, payload: UpdateImageRequest): Promise<GeneratedImage> {
    const updated = await imagesApi.updateImage(imageId, payload)
    images.value = sortImages(
      images.value.map((item) => {
        if (item.id === updated.id) {
          return updated
        }
        // ★ 设封面时后端会把同一篇文章里其他图的 is_cover 清掉（set_cover 做的），
        //   但响应体里只有被改的这一张 —— 本地也要跟着清，
        //   否则界面上会同时出现两个"封面"标记。
        if (updated.is_cover) {
          return { ...item, is_cover: false }
        }
        return item
      }),
    )
    return updated
  }

  /** 删除一张配图 */
  async function removeImage(imageId: string): Promise<void> {
    await imagesApi.deleteImage(imageId)
    images.value = images.value.filter((item) => item.id !== imageId)
  }

  /** 清空状态（换文章时用，避免旧文章的图闪一下） */
  function reset(): void {
    stopRequested = true
    images.value = []
    sectionCount.value = 0
    progress.value = { done: 0, total: 0, currentIndex: -1 }
    errorMessage.value = ''
    // ★ uploading 也要清：换文章的瞬间如果还有上传在跑，
    //   新文章的面板会一直显示"正在上传"并禁用按钮，而那个上传
    //   其实已经属于上一篇文章了。
    uploading.value = false
  }

  return {
    images,
    sectionCount,
    listLoading,
    generating,
    uploading,
    progress,
    errorMessage,
    succeededImages,
    usedSuggestionIndexes,
    fetchImages,
    generateOne,
    generateBatch,
    stopBatch,
    uploadOne,
    uploadBatch,
    updateOne,
    removeImage,
    reset,
  }
})
