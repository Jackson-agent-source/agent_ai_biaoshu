"""
文章业务层。

View 不允许直接操作 Article 模型 —— 所有写操作都经过这里。
这样做的好处：
1. View 保持很薄，只负责收请求、发响应；
2. 业务规则（比如"来源必须校验过""Markdown 要转安全 HTML"）集中在一处，
   不会因为某个接口忘了做而出现漏洞；
3. 以后要做"文章版本历史""审批流程"，只改这里。

注意：AgentDraft -> Article 的转换在 agent 层已经校验过来源了，
这里只负责"把校验后的数据落到数据库"。
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models import QuerySet

from apps.common.markdown_renderer import render_markdown_to_html
from apps.common.utils import truncate

from .models import Article

logger = logging.getLogger(__name__)


@transaction.atomic
def create_article_from_draft(
    *,
    user,
    conversation=None,
    agent_run=None,
    draft,
    validated_sources: list[dict[str, Any]],
    source_ids: list[str],
    risk_notes: list[str],
    model_name: str = "",
    tool_call_count: int = 0,
) -> Article:
    """把 Agent 产出的 ArticleDraft 落库成一篇文章草稿。

    参数：
        draft             Pydantic 的 ArticleDraft 对象
        validated_sources 已通过 SourceRegistry 校验的来源（只用于日志/确认）
        source_ids        这些来源在 Source 表里的 id 列表
        risk_notes        合并后的风险提示（模型给的 + 系统检测到的）

    返回：新建的 Article
    """
    content_markdown = (draft.body_markdown or "").strip()
    content_html = render_markdown_to_html(content_markdown)

    article = Article.objects.create(
        user=user,
        conversation=conversation,
        agent_run=agent_run,
        title=truncate(draft.title or "未命名文章", 200, ""),
        subtitle=truncate(draft.subtitle or "", 200, ""),
        summary=draft.summary or "",
        hook=draft.hook or "",
        outline=list(draft.outline or []),
        content_markdown=content_markdown,
        content_html=content_html,
        image_suggestions=list(draft.image_suggestions or []),
        source_ids=source_ids,
        risk_notes=risk_notes,
        # ★ V1 永远只生成草稿。绝不自动发布，这是产品红线。
        status=Article.Status.DRAFT,
        version=1,
        model_name=model_name,
        tool_call_count=tool_call_count,
    )

    logger.info(
        "文章已创建 article_id=%s user_id=%s sources=%s risk_notes=%s",
        article.id,
        getattr(user, "id", None),
        len(source_ids),
        len(risk_notes),
    )
    return article


@transaction.atomic
def update_article(*, article: Article, data: dict[str, Any]) -> Article:
    """用户手动编辑文章。

    允许修改：title / subtitle / summary / content_markdown / status /
             outline / image_suggestions
    禁止修改：user / agent_run / conversation / created_at / version
             （这些是"这篇文章从哪来的"的溯源信息，不能改）

    改了正文就重新渲染 HTML，并把版本号 +1，方便以后做版本对比。
    """
    editable_fields = {
        "title",
        "subtitle",
        "summary",
        "content_markdown",
        "status",
        "outline",
        "image_suggestions",
    }

    changed_fields: list[str] = []
    content_changed = False

    for field, value in data.items():
        if field not in editable_fields:
            continue
        if field == "content_markdown":
            content_changed = True
        setattr(article, field, value)
        changed_fields.append(field)

    if content_changed:
        article.content_html = render_markdown_to_html(article.content_markdown or "")

    if changed_fields:
        # 版本号只在内容真的变了的时候 +1
        article.version += 1
        article.save()

    logger.info("文章已更新 article_id=%s fields=%s", article.id, changed_fields)
    return article


@transaction.atomic
def archive_article(*, article: Article) -> Article:
    """软删除：把状态改成 archived，数据仍然保留。

    为什么不真删？用户可能只是不想在列表里看到它，误删就麻烦了。
    真删功能等有明确需求再做（需求文档允许 V1 先不提供删除）。
    """
    article.status = Article.Status.ARCHIVED
    article.save(update_fields=["status", "updated_at"])
    logger.info("文章已归档 article_id=%s", article.id)
    return article


def get_user_articles(*, user) -> QuerySet[Article]:
    """取某个用户的文章（不含已归档的，除非显式筛选）。"""
    return Article.objects.filter(user=user)


def get_user_stats(*, user) -> dict[str, int]:
    """首页统计数字。用一次聚合查询搞定，不要循环查数据库。"""
    from django.db.models import Count, Q

    result = Article.objects.filter(user=user).aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=Article.Status.DRAFT)),
        review=Count("id", filter=Q(status=Article.Status.REVIEW)),
        approved=Count("id", filter=Q(status=Article.Status.APPROVED)),
        archived=Count("id", filter=Q(status=Article.Status.ARCHIVED)),
    )
    return {key: value or 0 for key, value in result.items()}
