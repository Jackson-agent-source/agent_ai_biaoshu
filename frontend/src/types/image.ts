/**
 * AI 配图相关的数据类型。
 * 对应后端 /articles/<id>/images 和 /images/<id> 两组接口。
 *
 * ★ 一条重要的后端约定（写在 apps/imaging/serializers.py 里，这里复述一遍
 *   因为它直接决定前端怎么取图）：**图片本体不在 JSON 里**。
 *   接口只给元数据 + 一个 `file_url`，图片本身走 /media/…
 *   （生产由 nginx 从共享卷直发，本地由 vite 的 proxy 转给 Django）。
 *   所以别指望在响应里找到 base64 或者二进制。
 */

/** 配图状态 */
export type ImageStatus = 'pending' | 'running' | 'succeeded' | 'failed'

/**
 * 配图来源。
 *
 * ★ 只有两种：AI 生成的，和用户自己上传的实拍图。
 *   两者在**数据模型上完全是同一种东西**（同一张表、同一套字段、
 *   同一条推送链路），区别只在两处：
 *
 *     · 每日生成额度**不算**上传的图（那条额度是防生图 API 烧钱的，
 *       用户传自己的照片一分不花）
 *     · 界面上要能分辨（不然用户分不清哪张是自己传的）
 *
 * ★ 标签文案直接用后端给的 `source_display`，不在前端再维护一份映射 ——
 *   后端已经翻译过了，两份迟早不一致。
 */
export type ImageSource = 'ai' | 'upload'

/** 状态 -> 中文标签 */
export const IMAGE_STATUS_LABELS: Record<ImageStatus, string> = {
  pending: '等待中',
  running: '生成中',
  succeeded: '已生成',
  failed: '失败',
}

/** 状态 -> el-tag 的颜色 */
export const IMAGE_STATUS_TAG_TYPES: Record<
  ImageStatus,
  'primary' | 'success' | 'info' | 'danger'
> = {
  pending: 'info',
  running: 'primary',
  succeeded: 'success',
  failed: 'danger',
}

/** 一张配图（不含图片本体） */
export interface GeneratedImage {
  id: string
  /** 所属文章 id */
  article: string
  /** 来自文章的**第几条**配图建议（0 开始）。批量生成时用它判断哪几条已经出过图 */
  suggestion_index: number
  /** 配图建议原文 */
  suggestion: string
  /** 实际发给生图模型的提示词（画风前缀 + 建议），"图不对"的时候看它 */
  prompt: string
  /** 有的模型会把提示词改写一遍（OpenAI 的 revised_prompt） */
  revised_prompt: string
  status: ImageStatus
  status_display: string
  content_type: string
  /** 宽高可能为 null：图片探测失败时为 None（不影响使用，只是不显示尺寸） */
  width: number | null
  height: number | null
  byte_size: number
  /** 图注，会显示在公众号正文里图片下方 */
  caption: string
  /**
   * 插入位置：0 = 文章开头（第一个二级标题之前），N = 第 N 个 "## " 小节之后。
   * ★ 这个下标是按**正文里真实出现的 "## " 行**数的，不是 article.outline 的下标
   *   —— 两者可能对不上，后端刻意没有用 outline。
   */
  anchor_index: number
  /** 同一个位置插多张时的先后顺序 */
  sort_order: number
  /** 是否被设为封面（同一篇文章里只会有一张，后端会自己清掉其他的） */
  is_cover: boolean
  /** 这张图是 AI 生成的还是用户自己传的实拍图 */
  source: ImageSource
  /** 来源的中文标签（"AI 生成" / "自己上传"），直接用后端给的 */
  source_display: string
  /** 生图服务名 */
  provider: string
  model_name: string
  /**
   * 生图耗时。**上传的图永远是 null** —— 它没有"生成"这个过程，
   * 前端要拿它算"平均耗时"之类的统计时得先排除 upload。
   */
  duration_ms: number | null
  /** 上传到微信后拿到的 mmbiz.qpic.cn 地址；空字符串 = 还没传过 */
  wechat_url: string
  /** 失败原因（中文，后端已经把生图服务的报错翻译过了） */
  error_message: string
  /** 能直接塞进 <img src> 的地址；没图/失败时是空字符串 */
  file_url: string
  created_at: string
  updated_at: string
}

/** GET /articles/<id>/images 的返回数据 */
export interface ArticleImagesResponse {
  results: GeneratedImage[]
  /**
   * 正文里实际有几个 "## " 小节。
   * ★ 由后端算而不是前端自己数，是为了两边用**同一条规则**
   *   （比如代码块里的 "## " 不算小节这种事，只有一处实现才不会写岔）。
   */
  section_count: number
}

/**
 * POST /articles/<id>/images 的请求体。
 * ★ 一次只生成**一张** —— 一张要 30~90 秒，几张串起来会超过 nginx 的 600 秒。
 *   批量生成由 stores/image.ts 循环调用实现。
 */
export interface GenerateImageRequest {
  /** 第几条配图建议（0 开始），用于溯源和批量去重 */
  suggestion_index?: number
  /** 配图建议原文。和 prompt 二选一，都不给会被后端拒绝 */
  suggestion?: string
  /** 自定义提示词。留空则后端用 IMAGE_STYLE_PROMPT + suggestion 拼 */
  prompt?: string
  /** 插入位置。**不传**才是"让后端自己猜"，传 null 和不传是一样的效果 */
  anchor_index?: number | null
  caption?: string
}

/** PATCH /images/<id> 的请求体，只传要改的字段（后端会拒绝空请求体） */
export interface UpdateImageRequest {
  caption?: string
  anchor_index?: number
  sort_order?: number
  is_cover?: boolean
}

/**
 * POST /articles/<id>/images/upload 的附加字段。
 *
 * ★ 图片本体**不在这里** —— 它是 multipart 的 `file` 部分，
 *   走 FormData 传（见 api/images.ts）。这里是随图片一起提交的元信息。
 *
 * ★ 刻意**不给** suggestion_index / prompt：那两个是"生图"的概念，
 *   对一张用户自己拍的照片没有意义（后端也不会去读它们）。
 *   硬塞一个空值进去，只会让人以为上传的图也参与批量生成的去重。
 */
export interface UploadImageRequest {
  /** 图注，会显示在公众号正文里图片下方 */
  caption?: string
  /**
   * 插入位置，不传则由后端放在文章开头（0）。
   * ★ 实拍图多半是题图或现场图，放开头是合理的默认值，
   *   用户想挪到别处可以在配图列表里改。
   */
  anchor_index?: number
}