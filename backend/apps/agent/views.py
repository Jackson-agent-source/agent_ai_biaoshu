"""
Agent 相关的 API 接口。

View 的职责边界（很重要）：
    收请求 -> 用 Serializer 校验 -> 调用 Service -> 用统一信封返回

View 里**不允许**出现：
    - 直接调用大模型
    - 直接操作数据库（除了简单的查询）
    - 自己拼返回格式
    - 读取 API Key
"""

from __future__ import annotations

import logging

from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.agent.models import AgentRun, Source, ToolCall
from apps.agent.serializers import (
    AgentRunSerializer,
    ConversationSerializer,
    GenerateArticleRequestSerializer,
    MessageSerializer,
    SourceSerializer,
    ToolCallSerializer,
)
from apps.agent.services.agent_service import AgentService, get_or_create_conversation
from apps.common.exceptions import AppError
from apps.common.responses import paginated_response, success_response
from apps.conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


class AgentRunListCreateView(APIView):
    """POST /api/v1/agent/runs   创建一次生成任务

    GET  /api/v1/agent/runs   列出我的历史运行记录

    注意：POST 是**同步**的 —— 请求会一直等到文章生成完才返回（可能 30~120 秒）。
    V1 故意这么做：链路最短、最好调试。V1.1 再做 SSE 流式状态推送。
    """

    permission_classes = [IsAuthenticated]
    # ★ 限流作用在**整个 view 类**上，所以 GET（历史列表）也会被计入。
    #   对"防烧钱"这个目标来说无所谓 —— 额度给得宽（默认 10/hour），
    #   而正常使用不会一小时翻十次历史。
    #   比把 post() 拆成单独一个 view 简单得多，不值得为这点精度加结构。
    #   规则在 settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["agent_generate"]。
    throttle_scope = "agent_generate"

    def get(self, request: Request) -> Response:
        """列出当前用户的历史运行记录（分页）。"""
        queryset = (
            AgentRun.objects.filter(conversation__user=request.user)
            .select_related("conversation")
            .order_by("-created_at")
        )
        paginator = self._get_paginator()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AgentRunSerializer(page, many=True)
        return paginated_response(paginator, serializer.data)

    def post(self, request: Request) -> Response:
        """创建一次生成任务并同步等结果。"""
        serializer = GenerateArticleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 如果指定了会话，先确认这个会话属于当前用户（防越权）
        conversation = get_or_create_conversation(
            user=request.user,
            conversation_id=data.get("conversation_id"),
        )
        if data.get("conversation_id") and conversation is None:
            raise AppError("指定的会话不存在或不属于你。", code="CONVERSATION_NOT_FOUND")

        # View 不碰 Agent，一切交给 Service
        article, agent_run = AgentService.generate_article(
            user=request.user,
            request_data=data,
            conversation=conversation,
        )

        return success_response(
            {
                "run_id": str(agent_run.id),
                "article_id": str(article.id),
                "conversation_id": str(agent_run.conversation_id),
                "status": agent_run.status,
                "model_name": agent_run.model_name,
                "tool_call_count": agent_run.tool_call_count,
                "risk_notes": article.risk_notes,
            },
            status_code=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _get_paginator():
        from apps.common.pagination import StandardPagination

        return StandardPagination()


class AgentRunDetailView(APIView):
    """GET /api/v1/agent/runs/{run_id} —— 查询某次运行的状态。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, run_id) -> Response:
        agent_run = get_object_or_404(
            AgentRun.objects.filter(conversation__user=request.user).select_related("conversation"),
            id=run_id,
        )
        return success_response(AgentRunSerializer(agent_run).data)


class AgentRunToolCallsView(APIView):
    """GET /api/v1/agent/runs/{run_id}/tool-calls —— 工具调用过程。

    前端用这个接口渲染"Agent 执行过程"时间线：
        搜索 "AI Agent 2026" -> 获得 5 个来源 -> 读取 xxx -> 生成文章
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, run_id) -> Response:
        agent_run = get_object_or_404(
            AgentRun.objects.filter(conversation__user=request.user), id=run_id
        )

        tool_calls = ToolCall.objects.filter(agent_run=agent_run).order_by("sequence", "created_at")

        # 把这次运行的所有来源一次性查出来，避免 N+1 查询
        sources_by_run: dict = {}
        if tool_calls:
            source_list = list(Source.objects.filter(agent_run=agent_run).order_by("created_at"))
            sources_by_run[agent_run.id] = [
                {
                    "id": str(s.id),
                    "title": s.title,
                    "url": s.url,
                    "domain": s.domain,
                    "snippet": s.snippet,
                    "published_at": s.published_at,
                    "source_type": s.source_type,
                    "usage": s.usage,
                    "is_cited": s.is_cited,
                }
                for s in source_list
            ]

        serializer = ToolCallSerializer(
            tool_calls, many=True, context={"sources_by_run": sources_by_run}
        )
        return success_response(serializer.data)


class AgentRunSourcesView(APIView):
    """GET /api/v1/agent/runs/{run_id}/sources —— 这次运行检索到的所有来源。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, run_id) -> Response:
        agent_run = get_object_or_404(
            AgentRun.objects.filter(conversation__user=request.user), id=run_id
        )
        sources = Source.objects.filter(agent_run=agent_run).order_by("created_at")
        return success_response(SourceSerializer(sources, many=True).data)


class ConversationListView(APIView):
    """GET /api/v1/agent/conversations —— 我的创作会话列表。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        queryset = (
            Conversation.objects.filter(user=request.user)
            .annotate(
                message_count=Count("messages", distinct=True),
                run_count=Count("agent_runs", distinct=True),
            )
            .order_by("-updated_at")
        )
        paginator = AgentRunListCreateView._get_paginator()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ConversationSerializer(page, many=True)
        return paginated_response(paginator, serializer.data)


class ConversationMessagesView(APIView):
    """GET /api/v1/agent/conversations/{conversation_id}/messages —— 会话里的消息。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, conversation_id) -> Response:
        conversation = get_object_or_404(
            Conversation.objects.filter(user=request.user), id=conversation_id
        )
        messages = Message.objects.filter(conversation=conversation).order_by("created_at", "id")
        return success_response(MessageSerializer(messages, many=True).data)
