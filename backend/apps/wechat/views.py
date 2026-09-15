"""
公众号推送 API。

路由（挂在 config/urls.py 的 api/v1/ 下面）：
    GET  /api/v1/articles/<article_id>/wechat-publishes   推送历史（分页）
    POST /api/v1/articles/<article_id>/wechat-publishes   推送到草稿箱
    GET  /api/v1/wechat-publishes/<publish_id>            单条推送详情（含正文快照）

★ 这里做的永远是**建草稿**，不是发布。原因见 apps/wechat/services.py 的模块注释。

★ View 保持薄：业务全在已有的 services.publish_article_to_wechat 里。
  这样"检查 → 上传图 → 建草稿 → 记录"这个顺序只有一个地方实现，
  不会出现某个入口漏掉前置检查的情况。

别人的文章一律 404（而不是 403）—— 连"这个 id 存不存在"都不泄露，
和 apps/imaging/views.py、apps/articles/views.py 的约定一致。
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.articles.models import Article
from apps.common.pagination import StandardPagination
from apps.common.responses import paginated_response, success_response
from apps.imaging.models import GeneratedImage
from apps.wechat.models import WeChatPublish
from apps.wechat.serializers import (
    WeChatPublishCreateSerializer,
    WeChatPublishDetailSerializer,
    WeChatPublishListSerializer,
)
from apps.wechat.services import get_publish_history, publish_article_to_wechat

logger = logging.getLogger(__name__)


def _get_article(request: Request, article_id) -> Article:
    """取文章并确保属于当前用户。越权返回 404，不是 403。"""
    return get_object_or_404(Article, id=article_id, user=request.user)


class ArticleWeChatPublishView(APIView):
    """GET 推送历史 / POST 发起推送。"""

    permission_classes = [IsAuthenticated]
    # ★ 限流只加在 POST 上，见下面 get_throttles 的说明。
    throttle_scope = "wechat_publish"

    def get_throttles(self):
        """★ 必须重写：throttle_scope 是**类级**属性，直接挂在类上会把
        GET（查推送历史）也一起限流 —— 用户在详情页刷新几次历史就吃 429，
        而他什么都没干。

        agent 那边的生成接口没有这个问题，因为它是独立的 POST-only 视图。
        这里两个方法共用一个 URL，所以只能按方法分发。
        """
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return []

    def get(self, request: Request, article_id) -> Response:
        article = _get_article(request, article_id)
        # ★ 用 List 序列化器：不带 content_html。理由见 serializers 的模块注释。
        queryset = get_publish_history(article)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginated_response(
            paginator, WeChatPublishListSerializer(page, many=True).data
        )

    def post(self, request: Request, article_id) -> Response:
        """把文章推送到公众号草稿箱。

        ★ 这是**同步**的，一次要几十秒（要上传 N 张正文图 + 1 张封面）。
          前端调它的时候必须单独设一个长超时。

        失败时由 services 抛 AppError 子类：
            WECHAT_NOT_CONFIGURED      (400) 没配 WECHAT_APP_ID/SECRET
            WECHAT_PRECONDITION_FAILED (400) 还没有配图
            WECHAT_PUBLISH_ERROR       (502) 微信返回了错误码
        全局异常处理器会自动转成统一信封，所以这里**不写 try/except**。
        """
        article = _get_article(request, article_id)

        serializer = WeChatPublishCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 封面归属校验放在 View 里。
        # ★ 服务层其实不怕传错：get_cover_image 的 queryset 本来就按 article
        #   过滤过了，传别人的图片 id 只会**静默回退**到默认封面。
        #   静默回退对用户是坏体验 —— 他以为选了封面 B，实际推上去的是封面 A。
        #   所以这里显式挡一下，和 ArticleImagesView 校验 anchor_index 是同一个思路。
        cover_image_id = data.get("cover_image_id")
        if cover_image_id:
            owned = article.images.filter(
                id=cover_image_id, status=GeneratedImage.Status.SUCCEEDED
            ).exists()
            if not owned:
                raise ValidationError("指定的封面图不存在，或者还没有生成成功。")

        record = publish_article_to_wechat(
            article=article,
            user=request.user,
            cover_image_id=cover_image_id,
            force_new=data["force_new"],
        )

        return success_response(
            WeChatPublishDetailSerializer(record).data,
            message="已推送到公众号草稿箱。请到公众号后台审核后手动发布。",
            status_code=201,
        )


class WeChatPublishDetailView(APIView):
    """GET /api/v1/wechat-publishes/<publish_id> —— 单条推送详情。

    只有这个接口返回 content_html（排查排版问题要看当时真正发出去的 HTML）。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, publish_id) -> Response:
        record = get_object_or_404(WeChatPublish, id=publish_id, user=request.user)
        return success_response(WeChatPublishDetailSerializer(record).data)
