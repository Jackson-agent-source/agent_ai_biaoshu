"""文章序列化器。"""

from __future__ import annotations

from rest_framework import serializers

from apps.agent.models import Source
from apps.agent.serializers import SourceSerializer
from apps.articles.models import Article

# 用户可以手动改的字段
EDITABLE_FIELDS = ("title", "subtitle", "summary", "content_markdown", "status", "outline", "image_suggestions")


class ArticleListItemSerializer(serializers.ModelSerializer):
    """列表页用的精简版 —— 不带正文，减少传输量。"""

    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Article
        fields = (
            "id",
            "title",
            "subtitle",
            "summary",
            "status",
            "status_display",
            "version",
            "model_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ArticleDetailSerializer(serializers.ModelSerializer):
    """详情页用的完整版。"""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    sources = serializers.SerializerMethodField()
    agent_run_id = serializers.UUIDField(source="agent_run.id", read_only=True, allow_null=True)
    conversation_id = serializers.UUIDField(source="conversation.id", read_only=True, allow_null=True)

    class Meta:
        model = Article
        fields = (
            "id",
            "title",
            "subtitle",
            "summary",
            "hook",
            "outline",
            "content_markdown",
            "content_html",
            "image_suggestions",
            "risk_notes",
            "status",
            "status_display",
            "version",
            "model_name",
            "tool_call_count",
            "sources",
            "agent_run_id",
            "conversation_id",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    # ★ 这里**刻意没有** images 字段。配图走 GET /articles/<id>/images。
    #
    #   曾经加过，后来去掉了，理由记在这里免得被"顺手带上更方便"加回来：
    #
    #   1. 那个接口要额外返回 section_count（正文里真实有几个 "## " 小节），
    #      前端渲染"插入位置"下拉框、校验 anchor_index 都要用它，而详情接口
    #      没有这个值 —— 所以前端无论如何都得再请求一次。在这里返回 images
    #      就等于**每次打开文章都白查一遍**配图，拿到的东西立刻被丢掉。
    #
    #   2. 更关键的是**它会过期**。详情接口给的是页面加载那一刻的快照，而用户
    #      在配图面板里生成 / 删除一张图之后，"能不能推送到公众号"（要求至少
    #      一张成功配图）的答案就变了。用快照控制那个按钮的症状是：图明明
    #      生成好了按钮还是灰的，必须刷新整页才亮 —— 而且不会有任何报错，
    #      只会让人以为坏了。前端因此统一以配图接口为准（Pinia 的 image store），
    #      那份状态是活的。
    def get_sources(self, obj: Article) -> list[dict]:
        """返回这篇文章真正引用过的来源。

        Article.source_ids 存的是 Source 表的 id 列表。
        这里按 id 批量查出真实记录 —— 不依赖任何模型生成的内容，
        所以不可能出现"文章里挂着一个假链接"的情况。
        """
        ids = obj.source_ids or []
        if not ids:
            return []
        sources = Source.objects.filter(id__in=ids).order_by("created_at")
        return SourceSerializer(sources, many=True).data


class ArticleUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/articles/{id} 的请求体。

    只允许改这几个字段。user / agent_run / created_at / version
    这些"溯源信息"一律不准改，保证每篇文章都能追溯它是怎么来的。
    """

    title = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=False,
        error_messages={"blank": "标题不能为空。", "max_length": "标题最多 200 个字。"},
    )
    subtitle = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        error_messages={"max_length": "副标题最多 200 个字。"},
    )
    summary = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        error_messages={"max_length": "摘要最多 2000 个字。"},
    )
    content_markdown = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200_000,
        error_messages={"max_length": "正文太长了，最多 20 万字。"},
    )
    status = serializers.ChoiceField(
        choices=Article.Status.choices,
        required=False,
        error_messages={"invalid_choice": "状态只能是 draft、review、approved 或 archived。"},
    )
    outline = serializers.ListField(
        child=serializers.CharField(max_length=200, allow_blank=True),
        required=False,
        max_length=20,
        error_messages={"max_length": "大纲最多 20 条。"},
    )
    image_suggestions = serializers.ListField(
        child=serializers.CharField(max_length=500, allow_blank=True),
        required=False,
        max_length=20,
        error_messages={"max_length": "配图建议最多 20 条。"},
    )

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("没有需要更新的字段。")
        return attrs
