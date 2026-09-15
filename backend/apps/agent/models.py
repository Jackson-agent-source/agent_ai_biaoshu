"""
Agent 执行过程的三个记录表。

AgentRun  —— 一次完整的 Agent 执行（用户点一次"开始生成" = 一条 AgentRun）
ToolCall  —— 这次执行里 Agent 调用了哪些工具、参数是什么、成功还是失败
Source    —— 这次执行里 Agent 真正检索到 / 阅读过的网页

为什么要落库？
因为前端要展示"Agent 执行过程"，而且我们要能事后复盘：
它到底搜了什么、读了什么、有没有编造来源。

隐私提醒：Source 只存标题/URL/摘要，**不存第三方网页全文**。
"""

from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.db import models

from apps.conversations.models import Conversation


def make_url_hash(url: str) -> str:
    """为 URL 生成固定长度的指纹，用于去重。

    为什么不直接用 url 做唯一约束？
    因为 URL 可能很长，MySQL 的索引长度有限制（utf8mb4 下单列索引最长 3072 字节）。
    用 sha256 固定 64 个字符，既短又不会冲突。
    """
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()


class AgentRun(models.Model):
    """一次 Agent 执行。

    生命周期：pending -> running -> completed / failed / cancelled
    前端可以按 status 显示：正在分析 / 正在搜索 / 正在生成 / 已完成 / 执行失败。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "执行中"
        COMPLETED = "completed", "已完成"
        FAILED = "failed", "执行失败"
        CANCELLED = "cancelled", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="运行 ID")

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="agent_runs",
        verbose_name="所属会话",
    )

    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # 用户原始需求（已经拼装好的完整提示词文本）
    input_text = models.TextField("输入内容", blank=True, default="")

    model_name = models.CharField("使用的模型", max_length=120, blank=True, default="")

    tool_call_count = models.IntegerField("工具调用次数", default=0)

    # 只给用户看的中文提示，绝不写入 Traceback / API Key
    error_message = models.TextField("错误信息", blank=True, default="")

    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "Agent 运行"
        verbose_name_plural = "Agent 运行"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["conversation", "-created_at"])]

    def __str__(self) -> str:
        return f"AgentRun {self.id} [{self.status}]"


class ToolCall(models.Model):
    """一次工具调用记录。

    例如：
        tool_name = "web_search"，arguments = {"query": "2026 AI Agent 发展", "max_results": 5}
        tool_name = "read_webpage"，arguments = {"url": "https://example.com/post"}
    """

    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        # 达到研究预算上限、**根本没有执行**的调用。
        # 单独一个状态而不是复用 failed：它和"真的跑失败"是两回事，
        # 混在一起会让风险提示同时冒出"有 N 次调用失败"和"达到调用上限"两条，
        # 又重复又容易让人以为工具坏了。
        BLOCKED = "blocked", "已拦截"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="调用 ID")

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="tool_calls",
        verbose_name="所属运行",
    )

    tool_name = models.CharField("工具名称", max_length=64, db_index=True)

    arguments = models.JSONField("调用参数", default=dict, blank=True)

    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.RUNNING)

    # 只存摘要/截断文本，不存第三方网页全文
    result_preview = models.TextField("结果预览", blank=True, default="")

    # 工具执行的序号，方便前端按顺序展示时间线
    sequence = models.PositiveIntegerField("执行序号", default=0)

    error_message = models.TextField("错误信息", blank=True, default="")

    duration_ms = models.IntegerField("耗时(毫秒)", null=True, blank=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "工具调用"
        verbose_name_plural = "工具调用"
        ordering = ["sequence", "created_at"]
        indexes = [models.Index(fields=["agent_run", "sequence"])]

    def __str__(self) -> str:
        return f"{self.tool_name} [{self.status}]"


class Source(models.Model):
    """Agent 实际检索到 / 阅读过的来源。

    重要：这张表是"事实清单"。
    Agent 最终返回的 sources 必须能在这张表里找到对应 URL，否则会被丢弃。
    这是防止 AI 编造引用的关键一环。
    """

    class SourceType(models.TextChoices):
        SEARCH = "search", "搜索结果"
        WEBPAGE = "webpage", "已读网页"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="来源 ID")

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="sources",
        verbose_name="所属运行",
    )

    title = models.CharField("标题", max_length=500, blank=True, default="")
    url = models.URLField("链接", max_length=1000)
    url_hash = models.CharField("链接指纹", max_length=64, editable=False, db_index=True)
    domain = models.CharField("域名", max_length=200, blank=True, default="")
    snippet = models.TextField("摘要", blank=True, default="")
    published_at = models.DateTimeField("发布时间", null=True, blank=True)

    source_type = models.CharField("来源类型", max_length=20, choices=SourceType.choices, default=SourceType.SEARCH)

    # 这篇文章最终"怎么用"了这个来源，由模型在生成 ArticleDraft 时填写
    # 例如："提供了 2026 年 AI Agent 市场规模的统计数据"
    usage = models.CharField("用途说明", max_length=500, blank=True, default="")

    # 是否被最终文章引用（SourceRegistry 校验通过并出现在 draft.sources 里）
    is_cited = models.BooleanField("被文章引用", default=False)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "资料来源"
        verbose_name_plural = "资料来源"
        ordering = ["created_at"]
        # 同一次运行里，同一个 URL 只保留一条（自动去重）
        constraints = [
            models.UniqueConstraint(fields=["agent_run", "url_hash"], name="uniq_source_per_run_url"),
        ]
        indexes = [models.Index(fields=["agent_run", "created_at"])]

    def save(self, *args, **kwargs):
        # 保存时自动补全 url_hash 和 domain，调用方不用关心
        self.url_hash = make_url_hash(self.url)
        if not self.domain:
            from apps.common.utils import extract_domain

            self.domain = extract_domain(self.url)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title or self.url}"
