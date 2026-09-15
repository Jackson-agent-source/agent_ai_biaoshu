"""
内容创作 Agent —— 本项目的核心。

它用 LangChain v1 的 create_agent 构建，本质是一个"模型 + 工具 + 循环"：

    模型看到用户需求
        ↓
    判断需不需要外部资料
        ↓
    调用 web_search（如果需要）
        ↓
    看到搜索结果，判断哪些页面值得细读
        ↓
    调用 read_webpage
        ↓
    综合多个来源，规划结构、写作
        ↓
    调用结构化输出工具，返回 ArticleDraft
        ↓
    循环结束

我们要做的只有三件事：给它模型、给它工具、给它规矩（System Prompt + 限制）。
剩下的"什么时候搜、搜几次、读哪个页面"由模型自己决定 —— 这才是 Agent。

为什么用 create_agent 而不是 LangGraph？
V1 要的是"一个 Agent 自主完成一件事"，create_agent 正好是最合适的抽象。
V2 要做"大纲人工确认 -> 写作 -> 事实核查"这种固定流程时，再换成 LangGraph 的
StateGraph（create_agent 本身就是用 LangGraph 实现的，两者不冲突）。
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from apps.agent.agents.audit_middleware import ToolAuditMiddleware, ToolExecutionCollector
from apps.agent.agents.tool_budget_middleware import ToolBudgetMiddleware
from apps.agent.prompts.content_agent_prompt import CONTENT_AGENT_SYSTEM_PROMPT
from apps.agent.schemas.article import ArticleDraft
from apps.agent.services.model_factory import get_chat_model
from apps.agent.tools.read_webpage import read_webpage
from apps.agent.tools.web_search import web_search
from apps.common.exceptions import AgentExecutionError, ModelProviderError, StructuredOutputError

logger = logging.getLogger(__name__)


def build_content_agent(
    *,
    model: BaseChatModel | None = None,
    collector: ToolExecutionCollector | None = None,
):
    """构建内容创作 Agent。

    参数：
        model     可选的模型实例。不传就用 model_factory 按 .env 创建一个。
                  测试时传入假模型即可，不需要联网。
        collector 可选的工具调用收集器。不传就内部新建一个。

    返回：
        一个编译好的 LangGraph 图（CompiledStateGraph），调用方式是
        agent.invoke({"messages": [...]}, {"recursion_limit": N})
    """
    model = model or get_chat_model()
    collector = collector if collector is not None else ToolExecutionCollector()

    limits = settings.AGENT_LIMITS

    # ---- 中间件 1：记录每一次工具调用（写 ToolCall 表用）----
    audit = ToolAuditMiddleware(collector)

    # ---- 中间件 2：研究预算（防止 Agent 无限搜索下去）----
    # 为什么不用框架自带的 ToolCallLimitMiddleware？
    #   实测它会打断出稿：它注入的提示是 "Do not make additional tool calls."，
    #   而模型把"返回文章"也理解成一次工具调用，于是干脆什么都不做、
    #   返回一条空消息，整次生成判定失败。
    #   细节和实测过程见 tool_budget_middleware.py 的模块注释。
    # 我们自己的实现只说"别检索了"，并且明确告诉模型"现在直接出稿"。
    budget = ToolBudgetMiddleware(collector, limit=limits["MAX_TOOL_CALLS"])

    agent = create_agent(
        model=model,
        tools=[web_search, read_webpage],
        system_prompt=CONTENT_AGENT_SYSTEM_PROMPT,
        # 让模型最终必须返回符合 ArticleDraft 结构的结果，
        # 由框架负责校验和自动重试，我们不写任何正则解析。
        response_format=ArticleDraft,
        # ★ 顺序有讲究，不能反：budget 必须在外层，audit 在内层。
        #   反过来写会记出重复条目 —— audit 看到 budget 返回的那条
        #   "已达到上限"的 ToolMessage，会把它当成一次**成功的工具调用**
        #   再记一遍（实测 tool_call_count 从 1 变成 3）。
        #   budget 在外层时：拦下的调用根本进不到 audit，自然只有一条记录。
        middleware=[budget, audit],
    )
    return agent


def run_content_agent(
    *,
    user_prompt: str,
    collector: ToolExecutionCollector | None = None,
    model: BaseChatModel | None = None,
    trace_id: str = "-",
) -> tuple[ArticleDraft, list[BaseMessage]]:
    """跑一次 Agent，返回 (文章草稿, 完整消息列表)。

    参数：
        user_prompt 已经拼好的用户消息（见 prompts.build_user_prompt）
        collector   工具调用收集器，跑完以后从它里面读工具调用记录
        model       可选的模型实例（测试用）
        trace_id    日志里用的标识，一般传 AgentRun 的 id

    异常：
        ModelProviderError   模型配置有问题 / 模型服务调用失败
        StructuredOutputError 模型没有返回合法的 ArticleDraft
        AgentExecutionError  其他执行期错误

    说明：这里**不碰数据库**。落库的事由 AgentService 负责，
    这样 Agent 层可以脱离 Django 单独测试。
    """
    collector = collector if collector is not None else ToolExecutionCollector()

    logger.info("[AgentRun=%s] agent build start", trace_id)

    try:
        agent = build_content_agent(model=model, collector=collector)
    except ModelProviderError:
        # 模型没配置好，直接把可读的中文提示抛给上层
        raise
    except Exception as exc:
        logger.exception("[AgentRun=%s] Agent 构建失败", trace_id)
        raise AgentExecutionError("Agent 初始化失败，请检查模型配置。") from exc

    logger.info("[AgentRun=%s] agent invoke start", trace_id)

    try:
        result: dict[str, Any] = agent.invoke(
            {"messages": [{"role": "user", "content": user_prompt}]},
            # recursion_limit 是最后一道保险：万一模型陷入死循环，LangGraph 会强制中断
            {"recursion_limit": settings.AGENT_LIMITS["RECURSION_LIMIT"]},
        )
    except Exception as exc:
        logger.exception("[AgentRun=%s] Agent 执行失败", trace_id)
        raise _translate_agent_exception(exc) from exc

    messages: list[BaseMessage] = result.get("messages", [])
    draft = result.get("structured_response")

    if draft is None:
        # ★ 先别急着报错，试试能不能救回来。
        #   实测真实模型（DeepSeek）偶尔不调用结构化输出工具，
        #   而是把整篇文章**当普通正文直接写出来**。这条消息里没有 tool_calls，
        #   Agent 循环正常结束，但 structured_response 是 None —— 整次生成白跑。
        #   用户在网页上看到的就是"模型没有返回结构化的文章内容"。
        logger.warning("[AgentRun=%s] 模型没调用结构化输出工具，尝试补救", trace_id)
        draft, messages = _recover_structured_output(agent=agent, messages=messages)

    if draft is None:
        logger.error("[AgentRun=%s] 补救失败，模型仍未返回结构化结果", trace_id)
        raise StructuredOutputError(
            "模型没有返回结构化的文章内容。可能是模型不支持工具调用，"
            "或者本次对话中断了。请换一个支持 function calling 的模型再试。"
        )

    if not isinstance(draft, ArticleDraft):
        # 理论上不会发生，做一层保险
        try:
            draft = ArticleDraft.model_validate(draft)
        except Exception as exc:
            logger.exception("[AgentRun=%s] 结构化结果校验失败", trace_id)
            raise StructuredOutputError() from exc

    logger.info(
        "[AgentRun=%s] agent invoke completed tool_calls=%s title=%r",
        trace_id,
        collector.count,
        draft.title,
    )
    return draft, messages


# 补救时对模型说的话。
# 两个要点：
#   1. 明确说"你已经写完了"，否则它会从头再检索、再写一遍，白白多花一次钱和时间；
#   2. 明确点出工具名 —— 光说"用结构化输出"模型不一定知道指的是哪个工具。
RECOVERY_NUDGE = (
    "你上一条回复已经把文章正文直接写出来了，但没有调用结构化输出工具，"
    "所以系统这边收不到结果。\n\n"
    "请**不要重新搜索、也不要重新写一遍**，直接把你上一条回复里的内容，"
    "用 ArticleDraft 工具按字段返回一次即可（标题、摘要、开头钩子、大纲、正文等）。"
)


def _recover_structured_output(
    *,
    agent: Any,
    messages: list[BaseMessage],
) -> tuple[ArticleDraft | None, list[BaseMessage]]:
    """模型没调用结构化工具时的补救：把它已经写好的正文要回来。

    思路很简单 —— 模型其实**已经把文章写出来了**，只是写在了普通正文里。
    所以我们把已有的消息历史原样喂回去，再补一句话点醒它，
    让它改用 ArticleDraft 工具重新返回一次。多花一次模型调用，
    但比让用户白等一场、最后拿到一句"生成失败"要好得多。

    注意：这里复用**同一个 agent 实例**，所以研究预算中间件是有状态的，
    补救过程中模型如果想继续搜索，照样会被上限拦住，不会变成绕过限制的后门。

    返回 (草稿, 消息列表)。救不回来就返回 (None, 原消息列表)。
    """
    # 找最后一条"有正文"的模型消息 —— 文章就在里面
    last_text = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                last_text = message.content
                break

    if not last_text:
        # 连正文都没有，说明是别的问题（比如模型返回了空消息），救不了
        return None, messages

    logger.info("模型把正文写在普通消息里了（%s 字），要求它改用 ArticleDraft 返回", len(last_text))

    try:
        result: dict[str, Any] = agent.invoke(
            {
                "messages": [
                    *messages,
                    HumanMessage(content=RECOVERY_NUDGE),
                ]
            },
            {"recursion_limit": settings.AGENT_LIMITS["RECURSION_LIMIT"]},
        )
    except Exception:
        logger.exception("补救调用本身失败")
        return None, messages

    draft = result.get("structured_response")
    if isinstance(draft, ArticleDraft):
        logger.info("补救成功，拿到结构化文章 title=%r", draft.title)
        return draft, result.get("messages", messages)

    if draft is not None:
        try:
            return ArticleDraft.model_validate(draft), result.get("messages", messages)
        except Exception:
            logger.exception("补救拿到的结构化结果校验失败")

    return None, result.get("messages", messages)


def _translate_agent_exception(exc: Exception) -> Exception:
    """把底层异常翻译成用户能看懂的中文业务异常。

    原则：对用户只暴露"是什么问题、该怎么办"，
    完整堆栈只写进服务器日志（调用方已经 logger.exception 了）。
    """
    text = str(exc).lower()
    exc_name = type(exc).__name__

    # LangGraph 的递归上限
    if "recursion" in text or "GraphRecursionError" in exc_name:
        return AgentExecutionError(
            "Agent 思考步数过多被强制停止。请把主题写得更具体一些，或者把 MAX_TOOL_CALLS 调大。"
        )

    # 模型服务相关的报错，关键字来自各家 SDK
    model_keywords = (
        "api key", "apikey", "authentication", "unauthorized", "401",
        "rate limit", "429", "insufficient_quota", "quota",
        "connection", "timeout", "timed out", "connect error", "ssl",
        "model not found", "does not exist", "model_not_found", "502", "503",
    )
    if any(keyword in text for keyword in model_keywords):
        return ModelProviderError(
            "调用大模型失败。请检查 .env 里的 LLM_API_KEY、LLM_MODEL、LLM_BASE_URL "
            "是否正确，以及账户余额是否充足。"
        )

    return AgentExecutionError("文章生成过程中出现异常，请稍后重试。")
