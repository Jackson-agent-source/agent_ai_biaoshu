"""
公众号推送路由。

★ 这些 url 会被挂到 config/urls.py 的 `api/v1/` 下面，所以这里写的是相对路径。

★ 为什么叫 wechat-publishes 而不是 publishes：
    publish 这个名字太通用，将来接小红书/抖音时一定会被抢。
    带平台前缀和 Article 模型"只存内容、不存平台字段"的设计意图一致 ——
    一篇文章可以同时有 wechat_publishes 和别的平台的推送记录。

★ 为什么推送历史挂在文章下面（articles/<id>/wechat-publishes）：
    它天然是"这篇文章推过几次"，和 articles/<id>/images 是同一个形状。
    而单条详情用顶层前缀（wechat-publishes/<id>），
    因为前端手里只有推送 id 的时候，让它再带上文章 id 只是徒增出错机会。
"""

from __future__ import annotations

from django.urls import path

from apps.wechat.views import ArticleWeChatPublishView, WeChatPublishDetailView

urlpatterns = [
    path(
        "articles/<uuid:article_id>/wechat-publishes",
        ArticleWeChatPublishView.as_view(),
        name="article-wechat-publishes",
    ),
    path(
        "wechat-publishes/<uuid:publish_id>",
        WeChatPublishDetailView.as_view(),
        name="wechat-publish-detail",
    ),
]
