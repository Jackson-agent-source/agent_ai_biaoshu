"""
配图路由。

★ 这些 url 会被挂到 config/urls.py 的 `api/v1/` 下面，所以这里写的是相对路径。

两条独立的前缀：
    articles/<uuid:article_id>/images/   —— 挂在文章下（列表 + 生成 + 上传）
    images/<uuid:image_id>/              —— 直接按配图 id 操作

为什么不全挂在文章下（比如 articles/<id>/images/<image_id>/）：
    前端在配图工作台里改图注、拖位置时，手里只有配图 id，
    每次都要带上文章 id 很啰嗦，而且多一个"文章 id 和配图 id 对不上"的错误可能。
"""

from __future__ import annotations

from django.urls import path

from apps.imaging.views import (
    ArticleImagesView,
    ArticleImageUploadView,
    ImageDetailView,
    ImageFileView,
)

urlpatterns = [
    path(
        "articles/<uuid:article_id>/images",
        ArticleImagesView.as_view(),
        name="article-images",
    ),
    # ★ 上传实拍图。**必须写在上面那条后面**吗？不用 —— 两条路径
    #   一个是 ".../images" 一个是 ".../images/upload"，Django 的 path
    #   是整段精确匹配，不会出现"upload 被当成 article_id"的情况。
    #   （article_id 是 <uuid:...>，本来也匹配不上 "upload"。）
    path(
        "articles/<uuid:article_id>/images/upload",
        ArticleImageUploadView.as_view(),
        name="article-image-upload",
    ),
    path("images/<uuid:image_id>", ImageDetailView.as_view(), name="image-detail"),
    # ★ 兜底取图接口。正常的图片地址是 /media/...（nginx 直发），
    #   这条只在排查"图 404 了是不是 nginx 配错了"时用，见 ImageFileView。
    # ★ 顺序无所谓：Django 的 <uuid:...> 转换器不会匹配到 "file" 这种
    #   非 uuid 的字符串，和上面那条不会打架。写在后面只是读起来顺。
    path("images/<uuid:image_id>/file", ImageFileView.as_view(), name="image-file"),
]
