"""
配图路由。

★ 这些 url 会被挂到 config/urls.py 的 `api/v1/` 下面，所以这里写的是相对路径。

两条独立的前缀：
    articles/<uuid:article_id>/images/   —— 挂在文章下（列表 + 生成）
    images/<uuid:image_id>/              —— 直接按配图 id 操作

为什么不全挂在文章下（比如 articles/<id>/images/<image_id>/）：
    前端在配图工作台里改图注、拖位置时，手里只有配图 id，
    每次都要带上文章 id 很啰嗦，而且多一个"文章 id 和配图 id 对不上"的错误可能。
"""

from __future__ import annotations

from django.urls import path

from apps.imaging.views import ArticleImagesView, ImageDetailView, ImageFileView

urlpatterns = [
    path(
        "articles/<uuid:article_id>/images",
        ArticleImagesView.as_view(),
        name="article-images",
    ),
    path("images/<uuid:image_id>", ImageDetailView.as_view(), name="image-detail"),
    # ★ /file 必须放在 images/<uuid:image_id> **之后**也不会冲突 ——
    #   因为 Django 的 <uuid:...> 转换器不会匹配到 "file" 这种非 uuid 的字符串，
    #   所以顺序在这里无所谓。仍然显式写在后面，读起来顺。
    path("images/<uuid:image_id>/file", ImageFileView.as_view(), name="image-file"),
]
