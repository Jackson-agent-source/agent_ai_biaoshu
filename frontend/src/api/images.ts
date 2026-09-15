/**
 * AI 配图相关接口。
 * 对应后端 /articles/<id>/images 和 /images/<id> 两组路由。
 */

import { api } from './http'

import type {
  ArticleImagesResponse,
  GeneratedImage,
  GenerateImageRequest,
  UpdateImageRequest,
} from '@/types/image'

/**
 * 生成一张配图的专用超时（5 分钟）。
 *
 * ★ 这个接口是**同步**的：后端拿到请求后要真的去调生图模型，
 *   一张要 30~90 秒，慢的时候更久。
 *   用默认的 30 秒必被掐断，而且用户看到的只是一句"请求超时"，
 *   完全看不出图其实还在生成 —— 他会再点一次，于是又烧一张的钱。
 *
 * ★ 上游还有两道更长的超时兜着，这里不是随便写的数字：
 *   nginx 的 proxy_read_timeout 是 600 秒，后端 IMAGE_TIMEOUT 默认 300 秒。
 *   前端取 300 秒，正好和后端的超时对齐 —— 后端先放弃，
 *   前端就不会出现"其实后端已经失败了，但请求还挂在那儿"的空等。
 */
export const IMAGE_GENERATE_TIMEOUT = 300000

/** 列出某篇文章的全部配图（含 section_count，用来渲染"插入位置"下拉框） */
export function listArticleImages(articleId: string): Promise<ArticleImagesResponse> {
  return api.get<ArticleImagesResponse>(`/articles/${articleId}/images`)
}

/**
 * 生成一张配图（同步，30~90 秒）。
 * 一次只生成一张；批量生成由 stores/image.ts 循环调用本函数。
 */
export function generateImage(
  articleId: string,
  payload: GenerateImageRequest,
): Promise<GeneratedImage> {
  return api.post<GeneratedImage>(`/articles/${articleId}/images`, payload, {
    timeout: IMAGE_GENERATE_TIMEOUT,
  })
}

/** 改图注 / 插入位置 / 顺序 / 是否封面 */
export function updateImage(
  imageId: string,
  payload: UpdateImageRequest,
): Promise<GeneratedImage> {
  return api.patch<GeneratedImage>(`/images/${imageId}`, payload)
}

/** 删除一张配图（后端会连磁盘上的文件一起删掉，不用管清理） */
export function deleteImage(imageId: string): Promise<null> {
  return api.delete<null>(`/images/${imageId}`)
}
