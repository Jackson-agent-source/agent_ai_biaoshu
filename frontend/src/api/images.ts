/**
 * AI 配图相关接口。
 * 对应后端 /articles/<id>/images 和 /images/<id> 两组路由。
 */

import { api, MULTIPART_HEADERS } from './http'

import type {
  ArticleImagesResponse,
  GeneratedImage,
  GenerateImageRequest,
  UpdateImageRequest,
  UploadImageRequest,
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

/**
 * 上传实拍图的超时（2 分钟）。
 *
 * ★ 比默认的 30 秒长，因为这条路上**默认值不够用的情况是真实存在的**：
 *   正常的照片在浏览器里已经压到 1600px（几百 KB），一两秒就传完；
 *   但"前端没压成"时（老浏览器、canvas 被禁用）原图可能有 5~10MB，
 *   而服务器在**美国**、用户在国内，这种上行几 MB 要几十秒。
 *
 * ★ 又比 IMAGE_GENERATE_TIMEOUT(300 秒) 短得多：
 *   这条路上没有任何"服务端在慢慢干活"的成分，全是网络传输。
 *   传了两分钟还没完，基本就是用户自己断网了，让他重试比继续等更好。
 */
export const IMAGE_UPLOAD_TIMEOUT = 120000

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

/**
 * 上传一张实拍图（multipart）。
 *
 * ★ 和 generateImage 是**两条独立的路由**，不是一个接口的两种用法：
 *   那条是"调生图服务"（30~90 秒、算每日额度、慢），
 *   这条是"收一张用户自己的照片"（一秒、不算额度、快）。
 *   后端分成两条路由，前端也分成两个函数。
 *
 * ★★ headers 必须用 MULTIPART_HEADERS（把 Content-Type 整个去掉，让浏览器自己填）——
 *    不加的话这行 FormData 会被 JSON 序列化，**文件一个字节都发不出去**，
 *    而后端只会说一句"没有收到图片文件"。完整原因（以及三组写法的实测结果）
 *    写在 api/http.ts 那个常量上面，改之前先看。
 *
 * ★ 超时单独放宽到 2 分钟：正常上传只要一两秒（前端已经压过了），
 *   但"前端没压成、原图好几 MB + 跨国上行"时 30 秒真不够。
 */
export function uploadArticleImage(
  articleId: string,
  file: File,
  payload: UploadImageRequest = {},
): Promise<GeneratedImage> {
  const form = new FormData()
  // 字段名必须是 file —— 后端 request.FILES.get("file") 取的就是这个名字
  form.append('file', file)

  // 只把**真正有值**的字段塞进去。
  // 塞空字符串的话后端会当成"用户填了个空图注"，
  // 而我们要表达的是"这次没提这件事"。
  if (payload.caption) {
    form.append('caption', payload.caption)
  }
  if (payload.anchor_index !== undefined) {
    // FormData 的值只能是字符串或 Blob，数字会被隐式转成字符串 ——
    // 显式转一次，免得以后有人以为这里是数字类型
    form.append('anchor_index', String(payload.anchor_index))
  }

  return api.post<GeneratedImage>(`/articles/${articleId}/images/upload`, form, {
    headers: MULTIPART_HEADERS,
    timeout: IMAGE_UPLOAD_TIMEOUT,
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
