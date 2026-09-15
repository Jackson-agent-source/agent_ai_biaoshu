"""
文章 API。

所有查询都强制带上 `user=request.user` —— 这是防越权的关键。
少了这个过滤，A 用户就能通过猜 id 看到 B 用户的文章。
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.agent.models import AgentRun, Source
from apps.agent.serializers import AgentRunSerializer, SourceSerializer
from apps.articles.models import Article
from apps.articles.serializers import (
    ArticleDetailSerializer,
    ArticleListItemSerializer,
    ArticleUpdateSerializer,
)
from apps.articles.services import (
    archive_article,
    get_user_articles,
    get_user_stats,
    update_article,
)
from apps.common.pagination import StandardPagination
from apps.common.responses import paginated_response, success_response

logger = logging.getLogger(__name__)


class ArticleListView(APIView):
    """GET /api/v1/articles —— 文章历史。

    支持三种筛选，可以组合使用：
        ?status=draft      按状态
        ?keyword=AI        按标题/摘要关键词
        ?page=1&page_size=10
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        queryset = get_user_articles(user=request.user)

        # 按状态筛选
        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in Article.Status.values:
                return success_response(
                    {"count": 0, "page": 1, "page_size": 10, "total_pages": 0, "results": []},
                    message=f"未知的状态：{status_param}",
                )
            queryset = queryset.filter(status=status_param)
        else:
            # 默认不显示已归档的，想看要显式传 status=archived
            queryset = queryset.exclude(status=Article.Status.ARCHIVED)

        # 关键词搜索（标题 + 摘要）
        keyword = (request.query_params.get("keyword") or "").strip()
        if keyword:
            from django.db.models import Q

            queryset = queryset.filter(Q(title__icontains=keyword) | Q(summary__icontains=keyword))

        queryset = queryset.order_by("-created_at")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ArticleListItemSerializer(page, many=True)
        return paginated_response(paginator, serializer.data)


class ArticleStatsView(APIView):
    """GET /api/v1/articles/stats —— 仪表盘统计。

    注意：这个路由必须放在 /articles/<uuid:id> 之前，
    否则 "stats" 会被当成文章 id 去匹配。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        stats = get_user_stats(user=request.user)

        recent_articles = Article.objects.filter(user=request.user).order_by("-created_at")[:5]
        recent_runs = (
            AgentRun.objects.filter(conversation__user=request.user)
            .select_related("conversation")
            .order_by("-created_at")[:5]
        )

        return success_response(
            {
                **stats,
                "recent_articles": ArticleListItemSerializer(recent_articles, many=True).data,
                "recent_runs": AgentRunSerializer(recent_runs, many=True).data,
            }
        )


class ArticleDetailView(APIView):
    """GET / PATCH / DELETE /api/v1/articles/{article_id}"""

    permission_classes = [IsAuthenticated]

    def _get_article(self, request: Request, article_id) -> Article:
        """取文章，并确保它属于当前用户。

        get_object_or_404 配合 user 过滤：别人的文章会返回 404 而不是 403，
        这样连"这个 id 存不存在"都不会泄露。
        """
        return get_object_or_404(Article, id=article_id, user=request.user)

    def get(self, request: Request, article_id) -> Response:
        article = self._get_article(request, article_id)
        return success_response(ArticleDetailSerializer(article).data)

    def patch(self, request: Request, article_id) -> Response:
        """用户手动编辑文章。"""
        article = self._get_article(request, article_id)

        serializer = ArticleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        article = update_article(article=article, data=serializer.validated_data)
        return success_response(ArticleDetailSerializer(article).data, message="已保存。")

    def delete(self, request: Request, article_id) -> Response:
        """软删除：把状态改成 archived，数据保留。

        需求文档允许 V1 "软删除或暂时不提供删除"，我们选择软删除 ——
        用户误删还能找回来，比真删友好。
        """
        article = self._get_article(request, article_id)
        archive_article(article=article)
        return success_response(None, message="文章已归档。")


class ArticleSourcesView(APIView):
    """GET /api/v1/articles/{article_id}/sources —— 这篇文章引用过的来源。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, article_id) -> Response:
        article = get_object_or_404(Article, id=article_id, user=request.user)
        ids = article.source_ids or []
        if not ids:
            return success_response([])
        sources = Source.objects.filter(id__in=ids).order_by("created_at")
        return success_response(SourceSerializer(sources, many=True).data)
