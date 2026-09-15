"""
会话与消息模型。

Conversation = 一次内容创作会话（可以理解为"一个创作项目"）。
Message      = 这个会话里的一条条消息（用户说的话、Agent 的回复、工具调用记录）。

V1 的用法很简单：用户每次点"开始生成"，如果没有指定 conversation，
就新建一个 Conversation，然后把这次对话的消息都挂上去。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """一次内容创作会话。"""

    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        ARCHIVED = "archived", "已归档"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="会话 ID")

    # 直接复用 Django 自带的 User，不自己造用户系统
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name="所属用户",
    )

    title = models.CharField("会话标题", max_length=200, blank=True, default="")

    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "创作会话"
        verbose_name_plural = "创作会话"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.title or '未命名会话'} ({self.id})"


class Message(models.Model):
    """会话中的一条消息。

    role 表示"谁说的"，message_type 表示"这条消息是什么性质的内容"。
    两个字段配合使用，前端就能把对话渲染成聊天气泡 + 工具调用卡片。
    """

    class Role(models.TextChoices):
        USER = "user", "用户"
        ASSISTANT = "assistant", "Agent"
        SYSTEM = "system", "系统"
        TOOL = "tool", "工具"

    class MessageType(models.TextChoices):
        TEXT = "text", "普通文本"
        TOOL_CALL = "tool_call", "工具调用"
        TOOL_RESULT = "tool_result", "工具返回"
        ARTICLE = "article", "文章草稿"

    id = models.BigAutoField(primary_key=True)

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="所属会话",
    )

    role = models.CharField("角色", max_length=20, choices=Role.choices, db_index=True)
    content = models.TextField("内容", blank=True, default="")
    message_type = models.CharField(
        "消息类型",
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )

    # 存放结构化附加信息，例如：
    # {"tool_name": "web_search", "tool_arguments": {...}, "source_count": 5, "model_name": "..."}
    metadata = models.JSONField("附加信息", default=dict, blank=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "会话消息"
        verbose_name_plural = "会话消息"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        preview = (self.content or "")[:30]
        return f"[{self.role}] {preview}"
