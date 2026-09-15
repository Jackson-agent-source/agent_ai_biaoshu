"""文章路由，挂在 /api/v1/articles/ 下面。"""

from django.urls import path

from apps.articles import views

app_name = "articles"

urlpatterns = [
    path("", views.ArticleListView.as_view(), name="list"),
    # ⚠️ 顺序很重要：stats 必须写在 <uuid:article_id> 前面，
    # 否则 "stats" 会被当成 uuid 去匹配，直接报 404。
    path("stats/", views.ArticleStatsView.as_view(), name="stats"),
    path("<uuid:article_id>", views.ArticleDetailView.as_view(), name="detail"),
    path("<uuid:article_id>/sources", views.ArticleSourcesView.as_view(), name="sources"),
]
