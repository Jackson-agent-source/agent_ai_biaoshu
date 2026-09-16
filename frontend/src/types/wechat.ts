/**
 * 微信公众号推送相关的数据类型。
 * 对应后端 /articles/<id>/wechat-publishes 和 /wechat-publishes/<id>。
 *
 * ★★ 这个功能做的是**建草稿**，不是发布。★★
 *
 *    后端的 services 里根本没有调 freepublish/submit ——
 *    个人主体订阅号从 2025-07 起拿不到那个权限，API 层面就发不出去。
 *    所以整个流程是：
 *        点"推送到草稿箱" → 微信草稿箱里出现一份草稿 → 人工审核后手动发布
 *    这和产品红线（V1 绝不自动发布）正好一致，不是"还没做完"。
 *    界面上任何一句文案都不要写成"已发布"，否则用户会以为文章已经出去了。
 */

/** 推送状态 */
export type WeChatPublishStatus = 'running' | 'succeeded' | 'failed'

/** 状态 -> 中文标签 */
export const WECHAT_PUBLISH_STATUS_LABELS: Record<WeChatPublishStatus, string> = {
  running: '推送中',
  succeeded: '已创建草稿',
  failed: '失败',
}

/** 状态 -> el-tag 的颜色 */
export const WECHAT_PUBLISH_STATUS_TAG_TYPES: Record<
  WeChatPublishStatus,
  'primary' | 'success' | 'info' | 'danger'
> = {
  running: 'primary',
  succeeded: 'success',
  failed: 'danger',
}

/** 推送动作：add = 新建草稿，update = 更新已有草稿 */
export type WeChatPublishAction = 'add' | 'update' | ''

/** 一条推送记录（列表用） */
export interface WeChatPublish {
  id: string
  article: string
  status: WeChatPublishStatus
  status_display: string
  /** 失败时后端可能还没决定动作，所以允许空字符串 */
  action: WeChatPublishAction
  action_display: string
  /** 微信草稿的 media_id，靠它实现"再推一次是更新同一份" */
  media_id: string
  /** 推送时的标题快照 */
  title: string
  /** 本次推送带过去的正文图数量 */
  image_count: number
  /** 微信的 errcode 名（后端翻译过的，例如 wechat_invalid_ip） */
  error_code: string
  /** 微信给的中文说明，可以直接展示给用户 */
  error_message: string
  created_at: string
  updated_at: string
}

/**
 * 单条推送详情。
 * ★ 比列表多一个 content_html —— 那是**当时真正发出去的**整篇 HTML 快照，
 *   几十到几百 KB。只有详情接口会返回它，列表接口刻意不带
 *   （一页 10 条就是 MB 级响应体）。
 */
export interface WeChatPublishDetail extends WeChatPublish {
  content_html: string
}

/** POST /articles/<id>/wechat-publishes 的请求体 */
export interface PublishToWeChatRequest {
  /**
   * 指定哪张配图当封面。不传则由后端挑：
   * is_cover=True 的那张 → 第一张成功生成的。
   */
  cover_image_id?: string | null
  /**
   * true = 强制新建一份草稿。
   * 默认是**更新**这篇文章上次推的那份，草稿箱里始终只有一份
   *（否则"改一版推一次"会攒出一堆重复草稿）。
   * ★ 草稿在公众号后台被手工删掉时不用传这个 —— 后端拿到 40007
   *   会自己退回新建，因为"那份草稿没了"不该让推送失败。
   */
  force_new?: boolean
}

/**
 * 「复制到公众号」用的正文 HTML。
 * 对应 GET /articles/<id>/wechat-html。
 *
 * ★ 只有正文，**没有标题、作者和摘要** —— 公众号编辑器把它们当三个独立的
 *   输入框，塞进正文里反而要用户手动删掉。
 *
 * ★ 这条接口和推送那组**完全独立**：不需要配 WECHAT_APP_ID，
 *   也不需要公众号有草稿权限（48001）。它就是为"API 走不通"准备的退路 ——
 *   复制只复制文字是完全正当的用法，所以没有配图也是 200。
 */
export interface ArticleWeChatHtml {
  /** 已补好内联样式、图片按锚点插好的整篇正文 HTML */
  html: string
  /** 真的拼进 HTML 的配图张数（没有图片文件、或者取不到地址的不算） */
  image_count: number
  /**
   * 正文里有几个 `## ` 小节。
   * ★ 和后端 /articles/<id>/images 用的是**同一条切段规则**（含代码块围栏的处理）。
   */
  section_count: number
  /**
   * 非空就要提示用户（例如图片地址没配、有配图没有文件）。
   * 空字符串 = 一切正常。
   */
  warning: string
}
