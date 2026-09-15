"""
Agent 相关序列化器。

职责分工：
- 输入序列化器（GenerateArticleRequestSerializer）：**严格校验用户输入**，
  防止有人往接口里灌 10 万字把模型费用打爆。
- 输出序列化器：把数据库对象转成前端能直接用的 JSON。
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from apps.agent.models import AgentRun, Source, ToolCall
from apps.conversations.models import Conversation, Message

# 各字段的长度上限。和 .env 里的 MAX_USER_PROMPT_CHARS 一起构成双重保护。
TOPIC_MAX_LENGTH = 500
AUDIENCE_MAX_LENGTH = 100
GOAL_MAX_LENGTH = 200
TONE_MAX_LENGTH = 200
EXTRA_REQUIREMENTS_MAX_LENGTH = 2000

LENGTH_CHOICES = ("short", "medium", "long")


class GenerateArticleRequestSerializer(serializers.Serializer):
    """POST /api/v1/agent/runs 的请求体。"""

    topic = serializers.CharField(
        max_length=TOPIC_MAX_LENGTH,
        allow_blank=False,
        trim_whitespace=True,
        error_messages={
            "required": "请填写文章主题。",
            "blank": "文章主题不能为空。",
            "max_length": f"文章主题太长了，请控制在 {TOPIC_MAX_LENGTH} 个字以内。",
        },
    )

    audience = serializers.CharField(
        max_length=AUDIENCE_MAX_LENGTH,
        required=False,
        allow_blank=True,
        default="普通互联网用户",
        error_messages={"max_length": f"目标读者描述太长了，请控制在 {AUDIENCE_MAX_LENGTH} 个字以内。"},
    )

    goal = serializers.CharField(
        max_length=GOAL_MAX_LENGTH,
        required=False,
        allow_blank=True,
        default="知识科普",
        error_messages={"max_length": f"创作目标太长了，请控制在 {GOAL_MAX_LENGTH} 个字以内。"},
    )

    tone = serializers.CharField(
        max_length=TONE_MAX_LENGTH,
        required=False,
        allow_blank=True,
        default="自然、有观点",
        error_messages={"max_length": f"文章风格太长了，请控制在 {TONE_MAX_LENGTH} 个字以内。"},
    )

    length = serializers.ChoiceField(
        choices=LENGTH_CHOICES,
        required=False,
        default="medium",
        error_messages={"invalid_choice": "篇幅只能是 short、medium 或 long。"},
    )

    research_mode = serializers.BooleanField(required=False, default=True)

    extra_requirements = serializers.CharField(
        max_length=EXTRA_REQUIREMENTS_MAX_LENGTH,
        required=False,
        allow_blank=True,
        default="",
        error_messages={
            "max_length": f"补充要求太长了，请控制在 {EXTRA_REQUIREMENTS_MAX_LENGTH} 个字以内。"
        },
    )

    conversation_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        error_messages={"invalid": "会话 ID 格式不正确。"},
    )

    def validate(self, attrs: dict) -> dict:
        """整体校验：所有字段加起来不能超过 MAX_USER_PROMPT_CHARS。

        单个字段都合规、加起来却超长的情况很常见，所以需要这一层。
        目的是防止用户用超长 Prompt 消耗大量 token。

        注意：默认配置下，上面各字段的上限之和是
            500 + 100 + 200 + 200 + 2000 = 3000
        小于 MAX_USER_PROMPT_CHARS（5000），所以这条检查平时不会触发。
        它是一道**兜底**：万一以后有人把某个字段的上限调大，
        或者把 MAX_USER_PROMPT_CHARS 调小，这里仍然能拦住。
        想验证它生效，把 MAX_USER_PROMPT_CHARS 临时调小即可
        （见 tests/test_api.py 的 test_total_prompt_too_long_rejected）。
        """
        total = sum(
            len(str(attrs.get(field) or ""))
            for field in ("topic", "audience", "goal", "tone", "extra_requirements")
        )
        max_chars = settings.AGENT_LIMITS["MAX_USER_PROMPT_CHARS"]
        if total > max_chars:
            raise serializers.ValidationError(
                f"输入内容总长度 {total} 字，超过了上限 {max_chars} 字。请精简后重试。"
            )
        return attrs


class SourceSerializer(serializers.ModelSerializer):
    """来源。注意这里不会返回第三方网页全文 —— 我们本来就没存。"""

    class Meta:
        model = Source
        fields = (
            "id",
            "title",
            "url",
            "domain",
            "snippet",
            "published_at",
            "source_type",
            "usage",
            "is_cited",
            "created_at",
        )
        read_only_fields = fields


class ToolCallSerializer(serializers.ModelSerializer):
    """一次工具调用。前端用它渲染"Agent 执行过程"时间线。"""

    # 把这次调用新登记的来源也带上，前端可以显示"获得 5 个来源"
    sources = serializers.SerializerMethodField()

    class Meta:
        model = ToolCall
        fields = (
            "id",
            "tool_name",
            "arguments",
            "status",
            "result_preview",
            "error_message",
            "duration_ms",
            "sequence",
            "created_at",
            "sources",
        )
        read_only_fields = fields

    def get_sources(self, obj: ToolCall) -> list[dict]:
        """返回这次工具调用之后、与本次调用相关的来源。

        简化实现：返回该 AgentRun 中所有来源里，与本次调用参数相关的部分。
        V1 不做精确归属（那需要在 ToolCall 上加 M2M），够用即可。
        """
        sources = self.context.get("sources_by_run", {}).get(obj.agent_run_id, [])
        if obj.tool_name == "web_search":
            # 搜索：返回这次拿到的搜索结果条数对应的来源（按顺序取）
            return sources[: 10]
        if obj.tool_name == "read_webpage":
            url = (obj.arguments or {}).get("url", "")
            return [s for s in sources if s["url"] == url]
        return []


class AgentRunSerializer(serializers.ModelSerializer):
    """一次 Agent 运行的状态。"""

    article_id = serializers.SerializerMethodField()

    class Meta:
        model = AgentRun
        fields = (
            "run_id",
            "status",
            "model_name",
            "input_text",
            "tool_call_count",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
            "article_id",
            "conversation_id",
        )
        read_only_fields = fields

    # 数据库字段叫 id，但对外统一叫 run_id，和接口文档保持一致
    run_id = serializers.UUIDField(source="id", read_only=True)

    def get_article_id(self, obj: AgentRun) -> str | None:
        """这次运行产出的文章 id（可能没有，比如失败了）。"""
        article = obj.articles.first()
        return str(article.id) if article else None


class ConversationSerializer(serializers.ModelSerializer):
    """创作会话。"""

    message_count = serializers.IntegerField(read_only=True)
    run_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "status",
            "message_count",
            "run_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    """会话消息。"""

    class Meta:
        model = Message
        fields = ("id", "role", "content", "message_type", "metadata", "created_at")
        read_only_fields = fields
