/**
 * 微信公众号推送接口。
 * 对应后端 /articles/<id>/wechat-publishes 和 /wechat-publishes/<id>。
 *
 * ★ 这里做的永远是**建草稿**，不是发布。详见 types/wechat.ts 顶部的说明。
 *
 * ★ 另外还有一个和推送**完全独立**的 /articles/<id>/wechat-html
 *   （「复制到公众号」，见下面的 getWeChatHtml）——
 *   它一次微信接口都不碰，不需要任何凭据。
 */

import { api } from './http'

import type { Paginated } from '@/types/api'
import type {
  ArticleWeChatHtml,
  PublishToWeChatRequest,
  WeChatPublish,
  WeChatPublishDetail,
} from '@/types/wechat'

/**
 * 推送接口的专用超时（5 分钟）。
 *
 * ★ 也是同步的：一次要先把 N 张正文图传上微信换 mmbiz 地址，
 *   再传一张封面（封面要单独压成 900x500 的 JPG），
 *   最后才调 draft/add。图多的时候几十秒起步。
 *   默认的 30 秒不够用。
 *
 * ★ 第二次推送会快得多：已经传过的图（wechat_url 非空）直接复用，
 *   整个上传阶段几乎全部命中缓存。所以别因为"第一次很慢"就调小这个值。
 */
export const WECHAT_PUBLISH_TIMEOUT = 300000

/**
 * 把文章推送到公众号草稿箱。
 *
 * 后端可能返回的失败（错误码会通过 ApiError.code 带过来，message 是中文）：
 *   WECHAT_NOT_CONFIGURED      没配 WECHAT_APP_ID / WECHAT_APP_SECRET
 *   WECHAT_PRECONDITION_FAILED 文章还没有配图（微信要求草稿必须有封面）
 *   WECHAT_PUBLISH_ERROR       微信返回了错误码，例如 40164 IP 不在白名单
 */
export function publishToWeChat(
  articleId: string,
  payload: PublishToWeChatRequest = {},
): Promise<WeChatPublishDetail> {
  return api.post<WeChatPublishDetail>(`/articles/${articleId}/wechat-publishes`, payload, {
    timeout: WECHAT_PUBLISH_TIMEOUT,
  })
}

/**
 * 推送历史（分页，最新在前）。
 * ★ 列表里**没有** content_html，那是详情接口才返回的整篇 HTML 快照。
 */
export function listWeChatPublishes(
  articleId: string,
  page = 1,
  pageSize = 10,
): Promise<Paginated<WeChatPublish>> {
  return api.get<Paginated<WeChatPublish>>(`/articles/${articleId}/wechat-publishes`, {
    params: { page, page_size: pageSize },
  })
}

/** 单条推送详情（比列表多一份当时真正发出去的正文 HTML 快照） */
export function getWeChatPublish(publishId: string): Promise<WeChatPublishDetail> {
  return api.get<WeChatPublishDetail>(`/wechat-publishes/${publishId}`)
}

/**
 * 取「复制到公众号」用的正文 HTML。
 *
 * ★ 不设长超时：这条路一次外部调用都没有（不传图、不调微信），
 *   只是查库 + 渲染，默认的 30 秒绰绰有余。
 *
 * ★ 后端**不会**因为"没有配图"报错（那是推送接口的前置条件）——
 *   没有配图时返回的是 200 + image_count=0 的纯文字正文。
 */
export function getWeChatHtml(articleId: string): Promise<ArticleWeChatHtml> {
  return api.get<ArticleWeChatHtml>(`/articles/${articleId}/wechat-html`)
}
