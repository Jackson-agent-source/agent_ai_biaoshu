"""
公众号推送序列化器。

★ 最重要的一条：**列表里绝不带 content_html**。

    content_html 是本次推送**实际发出去的整篇 HTML 快照**，几十到几百 KB。
    列表接口一页 10 条一起带上，响应体会直接涨到 MB 级 ——
    这和当初"把图片二进制放进列表接口"是同一个错误，只是换了个字段。
    要看快照，走 wechat-publishes/<id> 的详情接口。

    存它的理由见 models.WeChatPublish.content_html 的注释（排查排版问题）。
"""

from __future__ import annotations

from rest_framework import serializers

from apps.wechat.models import WeChatPublish


class WeChatPublishListSerializer(serializers.ModelSerializer):
    """推送记录（列表用，不含正文快照）。"""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = WeChatPublish
        fields = [
            "id",
            "article",
            "status",
            "status_display",
            "action",
            "action_display",
            "media_id",
            "title",
            "image_count",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WeChatPublishDetailSerializer(WeChatPublishListSerializer):
    """单条推送记录（含正文快照）。只给详情接口用。"""

    class Meta(WeChatPublishListSerializer.Meta):
        fields = [*WeChatPublishListSerializer.Meta.fields, "content_html"]
        read_only_fields = fields


class WeChatPublishCreateSerializer(serializers.Serializer):
    """POST /api/v1/articles/<id>/wechat-publishes 的请求体。"""

    cover_image_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text=(
            "指定哪张配图当封面。不传则自动挑：先看 is_cover=True 的那张，"
            "没有就用第一张生成成功的。"
        ),
    )
    force_new = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "强制新建草稿。默认是**更新**这篇文章上一次推的那份，"
            "这样草稿箱里始终只有一份，不会改一版攒出一堆重复草稿。"
        ),
    )
