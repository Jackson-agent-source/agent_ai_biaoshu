"""
最终文章模型。

设计要点（关系到以后能不能扩展到小红书/抖音）：
- 表里存的是"内容"本身（标题、摘要、正文、大纲），
  而不是"微信公众号特有的字段"。以后加平台，只需要新增平台适配器，
  不用改这张表的核心结构。
- V1 只做微信公众号，所以 status 默认是 draft，绝不自动发布。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.agent.models import AgentRun
from apps.conversations.models import Conversation


class Article(models.Model):
    """一篇由 Agent 生成、并等待用户审阅的文章草稿。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        REVIEW = "review", "待审"
        APPROVED = "approved", "已通过"
        ARCHIVED = "archived", "已归档"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="文章 ID")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="articles",
        verbose_name="作者",
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        verbose_name="来源会话",
    )

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        verbose_name="来源运行",
    )

    title = models.CharField("标题", max_length=200)
    subtitle = models.CharField("副标题", max_length=200, blank=True, default="")
    summary = models.TextField("摘要", blank=True, default="")

    # 开头钩子（吸引读者的第一段）
    hook = models.TextField("开头钩子", blank=True, default="")

    # 文章大纲，例如 ["为什么这件事重要", "三个正在发生的变化", "普通人该怎么办"]
    outline = models.JSONField("大纲", default=list, blank=True)

    content_markdown = models.TextField("正文 Markdown", blank=True, default="")
    content_html = models.TextField("正文 HTML", blank=True, default="")  # 由 Markdown 渲染 + 清洗得到

    # 配图建议，例如 ["封面：一张 AI 与人类协作的抽象插画"]
    image_suggestions = models.JSONField("配图建议", default=list, blank=True)

    # 来源快照：存 Source 表的 id 列表。用 JSON 而不是 M2M，
    # 是因为来源属于"那一次 AgentRun"，删除运行记录不应该连带删除文章。
    source_ids = models.JSONField("来源 ID 列表", default=list, blank=True)

    # 风险提示：资料不足、数据无法确认、信息可能已变化等
    # 这个字段在需求文档的 Article 表里没写，但 ArticleDetail 页面要展示"风险提示"，
    # 而且 AgentDraft 里有 risk_notes，所以这里补上，避免信息丢失。
    risk_notes = models.JSONField("风险提示", default=list, blank=True)

    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    version = models.PositiveIntegerField("版本号", default=1)

    # 生成这篇文章用的模型名，ArticleHistory 页面上要展示
    model_name = models.CharField("使用的模型", max_length=120, blank=True, default="")

    # 这次生成的工具调用次数，详情页展示
    tool_call_count = models.PositiveIntegerField("工具调用次数", default=0)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "文章"
        verbose_name_plural = "文章"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return self.title
