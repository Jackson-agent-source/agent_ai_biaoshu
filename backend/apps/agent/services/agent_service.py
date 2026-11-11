"""
AgentService —— 后端最重要的业务层。

View 不允许直接调用 Agent，必须经过这里。原因：
1. 一次生成要写 5 张表（AgentRun / ToolCall / Source / Message / Article），
   还要处理失败、限制、来源校验。这套流程必须只有一份实现。
2. View 只该做三件事：接请求、校验参数、返回响应。

完整流程（对应需求文档第十三节的 16 个步骤）：

    1  创建 AgentRun(status=pending)
    2  更新为 running，记录 started_at
    3  把用户输入存成 Message(role=user)
    4  拼装 Agent 输入（主题/读者/目标/风格/篇幅/补充要求/当前日期）
    5  激活 SourceRegistry
    6  运行 Agent（模型自己决定要不要调用工具）
    7  收集工具调用记录 -> 写 ToolCall 表
    8  把真实来源写进 Source 表
    9  拿到 ArticleDraft
    10 校验结构化输出
    11 用 SourceRegistry 校验 draft.sources，剔除模型编造的链接
    12 Markdown -> 安全 HTML
    13 创建 Article(status=draft)
    14 存 Agent 的 assistant Message
    15 AgentRun 标记 completed
    16 返回 Article

出错时：AgentRun 标记 failed，error_message 存中文提示，
        完整堆栈只写服务器日志，绝不返回给前端。
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.agent.agents.audit_middleware import (
    ToolExecutionCollector,
    find_blocked_tool_calls,
)
from apps.agent.agents.content_agent import run_content_agent
from apps.agent.models import AgentRun, Source, ToolCall, make_url_hash
from apps.agent.prompts.content_agent_prompt import build_user_prompt
from apps.agent.services.model_factory import get_model_name
from apps.agent.services.source_registry import (
    SourceRegistry,
    activate,
    deactivate,
)
from apps.articles.models import Article
from apps.articles.services import create_article_from_draft
from apps.common.exceptions import (
    AppError,
    AgentExecutionError,
    DailyQuotaExceededError,
)
from apps.common.utils import current_date_text, truncate
from apps.conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


class AgentService:
    """内容生成服务的入口。所有方法都是静态方法，不需要实例化。"""

    # ------------------------------------------------------------------
    # 对外主入口
    # ------------------------------------------------------------------
    @staticmethod
    def generate_article(
        *,
        user,
        request_data: dict[str, Any],
        conversation: Conversation | None = None,
    ) -> tuple[Article, AgentRun]:
        """根据用户需求生成一篇文章草稿。

        参数：
            user          当前登录用户
            request_data  已经过 Serializer 校验的请求数据
            conversation  可选，不传就新建一个会话

        返回：(Article, AgentRun)

        异常：AppError 的子类（前端会收到 code + 中文 message + request_id）
        """
        limits = settings.AGENT_LIMITS
        model_name = get_model_name(user=user)

        # ---- 步骤 0：每日额度检查 ----
        # ★ 放在最前面（连 Conversation 都还没建）是因为这一层拦的是"花不花钱"，
        #   而不是"记录写没写成"。额度用完了就不该产生任何副作用。
        AgentService._enforce_daily_quota(user=user)

        # ---- 步骤 1：准备会话 ----
        conversation = conversation or AgentService._create_conversation(
            user=user, topic=request_data.get("topic", "")
        )

        # ---- 步骤 1：创建 AgentRun（pending）----
        agent_run = AgentRun.objects.create(
            conversation=conversation,
            status=AgentRun.Status.PENDING,
            model_name=model_name,
        )
        trace = str(agent_run.id)
        logger.info(
            "[AgentRun=%s] 开始生成 user_id=%s conversation_id=%s topic=%r",
            trace,
            getattr(user, "id", None),
            conversation.id,
            truncate(request_data.get("topic", ""), 80, ""),
        )

        try:
            # ---- 步骤 2：标记 running ----
            agent_run.status = AgentRun.Status.RUNNING
            agent_run.started_at = timezone.now()
            agent_run.save(update_fields=["status", "started_at"])

            # ---- 步骤 3~4：拼装 Agent 输入 ----
            user_prompt = build_user_prompt(
                topic=request_data.get("topic", ""),
                audience=request_data.get("audience", ""),
                goal=request_data.get("goal", ""),
                tone=request_data.get("tone", ""),
                length=request_data.get("length", "medium"),
                research_mode=request_data.get("research_mode", True),
                extra_requirements=request_data.get("extra_requirements", ""),
                current_date=current_date_text(),
                max_tool_calls=limits["MAX_TOOL_CALLS"],
            )
            agent_run.input_text = user_prompt
            agent_run.save(update_fields=["input_text"])

            Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                message_type=Message.MessageType.TEXT,
                content=user_prompt,
                metadata={
                    "topic": request_data.get("topic", ""),
                    "length": request_data.get("length", "medium"),
                    "research_mode": request_data.get("research_mode", True),
                },
            )

            # ---- 步骤 5~6：激活来源登记簿，运行 Agent ----
            registry = SourceRegistry()
            collector = ToolExecutionCollector()
            token = activate(registry)
            try:
                draft, _messages = run_content_agent(
                    user=user,
                    user_prompt=user_prompt,
                    collector=collector,
                    trace_id=trace,
                    json_fallback=bool(request_data.get("trend_json_fallback", False)),
                )
            except Exception:
                # ★ 失败也要把**已经发生**的工具调用记下来。
                #   不然失败运行的时间线永远是空的：用户不知道研究走到了哪一步，
                #   我们也拿不到现场，只能靠猜（这个坑真踩过）。
                #   用 try 包住，避免这里再出错把原始异常顶掉。
                AgentService._safe_persist_tool_calls(agent_run=agent_run, collector=collector)
                AgentService._safe_persist_sources(agent_run=agent_run, registry=registry)
                raise
            finally:
                # 无论成功失败都要还原，否则会影响同一个线程里的后续请求
                deactivate(token)

            # ---- 步骤 7：写 ToolCall ----
            AgentService._persist_tool_calls(agent_run=agent_run, collector=collector)

            # ---- 步骤 8：写 Source ----
            saved_sources = AgentService._persist_sources(agent_run=agent_run, registry=registry)

            # ---- 步骤 10~11：校验来源 ----
            validated, source_risk_notes = registry.validate_draft_sources(draft.sources)

            # ---- 收集系统侧的风险提示 ----
            system_risk_notes = AgentService._collect_system_risk_notes(
                request_data=request_data,
                registry=registry,
                collector=collector,
                messages=_messages,
                validated_count=len(validated),
            )
            risk_notes = list(draft.risk_notes or []) + source_risk_notes + system_risk_notes

            # 把校验通过的来源标记为"被引用"，并写入用途说明
            source_ids = AgentService._mark_cited_sources(
                saved_sources=saved_sources, validated=validated
            )

            # ---- 步骤 12~13：Markdown -> HTML，创建 Article ----
            article = create_article_from_draft(
                user=user,
                conversation=conversation,
                agent_run=agent_run,
                draft=draft,
                validated_sources=validated,
                source_ids=source_ids,
                risk_notes=risk_notes,
                model_name=model_name,
                tool_call_count=collector.count,
            )

            # ---- 步骤 14：存 assistant Message ----
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                message_type=Message.MessageType.ARTICLE,
                content=draft.body_markdown or "",
                metadata={
                    "article_id": str(article.id),
                    "title": draft.title,
                    "summary": draft.summary,
                    "model_name": model_name,
                    "tool_call_count": collector.count,
                    "source_count": len(source_ids),
                    "risk_notes": risk_notes,
                },
            )

            # ---- 步骤 15：标记完成 ----
            agent_run.status = AgentRun.Status.COMPLETED
            agent_run.tool_call_count = collector.count
            agent_run.completed_at = timezone.now()
            agent_run.save(update_fields=["status", "tool_call_count", "completed_at"])

            conversation.updated_at = timezone.now()
            conversation.save(update_fields=["updated_at"])

            logger.info(
                "[AgentRun=%s] 生成成功 article_id=%s tool_calls=%s sources=%s",
                trace,
                article.id,
                collector.count,
                len(source_ids),
            )
            return article, agent_run

        except AppError as exc:
            # 业务异常：message 是可以给用户看的
            AgentService._mark_failed(agent_run=agent_run, message=exc.message)
            logger.warning("[AgentRun=%s] 生成失败（业务异常）: %s", trace, exc.message)
            raise

        except Exception as exc:
            # 意料之外的异常：完整堆栈进日志，用户只看通用提示
            AgentService._mark_failed(agent_run=agent_run, message="文章生成过程中出现异常，请稍后重试。")
            logger.exception("[AgentRun=%s] 生成失败（未预期异常）", trace)
            raise AgentExecutionError() from exc

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------
    @staticmethod
    def _enforce_daily_quota(*, user) -> None:
        """每日生成额度检查。0 = 不限制。

        ★ 为什么不用缓存计数：缓存是"可以丢的" —— 重启、清缓存、换 worker
          都会让它归零，一归零限流就形同虚设。这里要的是一个**硬账本**：
          直接数 AgentRun 表里的真实行数，绕不过去，也不需要任何缓存。
        ★ 为什么不新建一张额度表：AgentRun 里已经有全部信息（谁、什么时候），
          直接 count 就够了。零迁移 = 升级时不会出岔子，这是最省心的做法。
        ★ 为什么统计**所有** AgentRun 而不只是成功的：失败的那次照样真实调用了
          大模型、照样花钱。对大模型计费来说，"失败"和"成功"没有区别 ——
          只按成功计数等于给失败留了个免费刷的洞。
        ★ 并发说明：两个请求同时进来，可能都通过检查，最终超出 1~2 次。
          一台服务器、几个人用，这个误差完全可以接受。
          要严格的话得加行锁（select_for_update），不值得。
        """
        per_user = settings.AGENT_DAILY_QUOTA_PER_USER
        global_cap = settings.AGENT_DAILY_QUOTA_GLOBAL
        if per_user <= 0 and global_cap <= 0:
            return

        # ★ created_at 存的是 UTC（USE_TZ=True），必须先把"今天零点"按**上海时区**
        #   算出来。直接用 UTC 的零点会变成北京时间早上 8 点重置额度 ——
        #   这种 bug 平时完全看不出来，只在用户抱怨"我的额度半夜就没了"时才暴露。
        #   localtime() 返回的是 aware datetime，Django 查询时会自动转回 UTC 比较，
        #   所以这里不需要手工做任何时区转换。
        day_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

        if per_user > 0:
            used = AgentRun.objects.filter(
                conversation__user=user, created_at__gte=day_start
            ).count()
            if used >= per_user:
                logger.warning(
                    "用户达到每日额度 user_id=%s used=%s limit=%s",
                    getattr(user, "id", None),
                    used,
                    per_user,
                )
                raise DailyQuotaExceededError(
                    f"你今天已经生成了 {used} 篇文章，达到每日上限（{per_user} 篇）。"
                    f"请明天再试，或联系管理员调整额度。"
                )

        if global_cap > 0:
            # 注意这条没有 conversation__user 过滤，是全站计数。
            # AgentRun.Meta.indexes 里目前只有 ["conversation", "-created_at"]，
            # 所以这条会全表扫描 —— 几千行以内无所谓。
            # 等真的上万行了，再给 AgentRun.Meta.indexes 补一个
            # models.Index(fields=["-created_at"])。
            total = AgentRun.objects.filter(created_at__gte=day_start).count()
            if total >= global_cap:
                logger.warning(
                    "全站每日生成总量已达上限 total=%s limit=%s", total, global_cap
                )
                raise DailyQuotaExceededError(
                    "今天全站的文章生成额度已经用完，请明天再试。"
                )

    @staticmethod
    def _create_conversation(*, user, topic: str) -> Conversation:
        """新建一个创作会话，标题就用主题的前 50 个字。"""
        title = truncate(topic or "未命名会话", 50, "")
        conversation = Conversation.objects.create(
            user=user,
            title=title,
            status=Conversation.Status.ACTIVE,
        )
        logger.info("新建会话 conversation_id=%s title=%r", conversation.id, title)
        return conversation

    @staticmethod
    def _safe_persist_tool_calls(
        *, agent_run: AgentRun, collector: ToolExecutionCollector
    ) -> None:
        """失败路径下写工具调用记录，写不进去也不能把原始异常顶掉。"""
        try:
            AgentService._persist_tool_calls(agent_run=agent_run, collector=collector)
        except Exception:
            logger.exception("[AgentRun=%s] 失败路径写工具调用记录时出错", agent_run.id)

    @staticmethod
    def _persist_tool_calls(*, agent_run: AgentRun, collector: ToolExecutionCollector) -> None:
        """把收集到的工具调用记录批量写进 ToolCall 表。"""
        if not collector.records:
            logger.info("[AgentRun=%s] 本次没有调用任何工具", agent_run.id)
            return

        # 显式映射，不用 "不是 success 就是 failed" 那种偷懒写法 ——
        # 被预算拦下的调用必须落成 blocked，否则风险提示会误报成"工具调用失败"。
        status_map = {
            "success": ToolCall.Status.SUCCESS,
            "failed": ToolCall.Status.FAILED,
            "blocked": ToolCall.Status.BLOCKED,
        }

        objects = [
            ToolCall(
                agent_run=agent_run,
                tool_name=record.tool_name,
                arguments=record.arguments,
                status=status_map.get(record.status, ToolCall.Status.FAILED),
                result_preview=record.result_preview,
                error_message=record.error_message,
                duration_ms=record.duration_ms,
                sequence=record.sequence,
            )
            for record in collector.records
        ]
        ToolCall.objects.bulk_create(objects)
        logger.info("[AgentRun=%s] 已记录工具调用 %s 次", agent_run.id, len(objects))

    @staticmethod
    def _safe_persist_sources(*, agent_run: AgentRun, registry: SourceRegistry) -> None:
        """失败路径下写来源记录，同样不能顶掉原始异常。"""
        try:
            AgentService._persist_sources(agent_run=agent_run, registry=registry)
        except Exception:
            logger.exception("[AgentRun=%s] 失败路径写来源记录时出错", agent_run.id)

    @staticmethod
    def _persist_sources(*, agent_run: AgentRun, registry: SourceRegistry) -> dict[str, Source]:
        """把来源登记簿里的所有 URL 写进 Source 表。

        返回 {url: Source} 的映射，方便后面按 URL 找记录。

        去重靠数据库的 (agent_run, url_hash) 唯一约束 —— 用 ignore_conflicts
        让重复的 URL 直接跳过，不会报错。
        """
        sources = registry.all()
        if not sources:
            return {}

        objects = [
            Source(
                agent_run=agent_run,
                title=truncate(item.title or item.url, 500, ""),
                url=item.url,
                url_hash=make_url_hash(item.url),
                domain=item.domain,
                snippet=truncate(item.snippet, settings.AGENT_LIMITS["SOURCE_SNIPPET_CHARS"], ""),
                published_at=item.published_at,
                source_type=(
                    Source.SourceType.WEBPAGE if item.was_read else Source.SourceType.SEARCH
                ),
            )
            for item in sources
        ]
        # ignore_conflicts：同一个 URL 重复出现时跳过，不抛异常
        Source.objects.bulk_create(objects, ignore_conflicts=True)

        saved = {
            source.url: source
            for source in Source.objects.filter(agent_run=agent_run)
        }
        logger.info("[AgentRun=%s] 已记录来源 %s 条", agent_run.id, len(saved))
        return saved

    @staticmethod
    def _mark_cited_sources(*, saved_sources: dict[str, Source], validated: list[dict]) -> list[str]:
        """把"确实被文章引用"的来源标记出来，返回它们的 id 列表。

        只有校验通过的来源才会被标记，也只有它们会写进 Article.source_ids。
        """
        cited_ids: list[str] = []
        to_update: list[Source] = []

        for item in validated:
            source = saved_sources.get(item["url"])
            if source is None:
                continue
            source.is_cited = True
            source.usage = truncate(item.get("usage", ""), 500, "")
            to_update.append(source)
            cited_ids.append(str(source.id))

        if to_update:
            Source.objects.bulk_update(to_update, ["is_cited", "usage"])

        return cited_ids

    @staticmethod
    def _collect_system_risk_notes(
        *,
        request_data: dict[str, Any],
        registry: SourceRegistry,
        collector: ToolExecutionCollector,
        messages: list,
        validated_count: int,
    ) -> list[str]:
        """系统自己检测出来的风险提示（不是模型给的）。

        这比模型的自述更可靠 —— 模型可能过度自信，但系统知道事实：
        比如"搜索工具报错了""达到调用上限了""一个来源都没拿到"。
        """
        notes: list[str] = []
        research_mode = request_data.get("research_mode", True)

        # 1) 用户要求联网研究，但一个来源都没拿到
        if research_mode and len(registry) == 0:
            notes.append(
                "本次未能获取到任何外部资料，文章内容基于模型已有知识生成，"
                "其中的事实与数据可能已经过时，请自行核实。"
            )

        # 2) 工具调用过程中出现过失败
        failed_tools = [r for r in collector.records if r.status == "failed"]
        if failed_tools:
            failed_names = "、".join(sorted({r.tool_name for r in failed_tools}))
            notes.append(
                f"研究过程中有 {len(failed_tools)} 次工具调用失败（{failed_names}），"
                "可能有部分信息未能获取。"
            )

        # 3) 达到了工具调用上限（说明研究被强制中断了）
        blocked = find_blocked_tool_calls(messages, collector)
        if blocked:
            notes.append(
                "达到研究调用上限，部分信息可能需要进一步核实。"
            )

        # 4) 检索到了来源，但文章一个都没引用
        if len(registry) > 0 and validated_count == 0 and research_mode:
            notes.append(
                "检索到了外部资料，但文章没有引用任何来源，"
                "请检查文中涉及事实的部分是否有依据。"
            )

        return notes

    @staticmethod
    def _mark_failed(*, agent_run: AgentRun, message: str) -> None:
        """把 AgentRun 标记为失败。用 try 包住，避免这里再报错导致原始异常丢失。"""
        try:
            agent_run.status = AgentRun.Status.FAILED
            agent_run.error_message = message
            agent_run.completed_at = timezone.now()
            agent_run.save(update_fields=["status", "error_message", "completed_at"])
        except Exception:  # pragma: no cover
            logger.exception("[AgentRun=%s] 更新失败状态时出错", agent_run.id)


# ---------------------------------------------------------------------------
# 供 View 使用的小工具
# ---------------------------------------------------------------------------
@transaction.atomic
def get_or_create_conversation(*, user, conversation_id: str | None) -> Conversation | None:
    """按 id 取会话，并确认它属于当前用户。

    安全要点：不能让 A 用户往 B 用户的会话里写东西（越权）。
    查不到或者不属于自己，都返回 None，由调用方决定怎么办。
    """
    if not conversation_id:
        return None
    return Conversation.objects.filter(id=conversation_id, user=user).first()
