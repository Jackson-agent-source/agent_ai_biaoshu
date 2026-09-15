"""
工具调用审计中间件。

作用：**在不侵入工具代码的前提下**，记录 Agent 每一次工具调用的全过程。

为什么用中间件而不是让工具自己写数据库？
1. 工具保持纯粹：输入 -> 输出，方便单独测试。
2. 工具不需要知道 Django 模型的存在（需求文档明确要求"Tool 不直接随意写数据库"）。
3. 一处代码记录所有工具，以后新增工具不用改记录逻辑。

LangChain 的中间件钩子 wrap_tool_call 会在每次工具执行前后被调用，
我们在这里计时、抓参数、抓结果、抓异常。

注意（实测结论）：这个钩子**不会**被结构化输出工具（ArticleDraft）触发，
只会被真实工具（web_search / read_webpage）触发，所以计数天然准确。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionRecord:
    """一次工具执行的完整记录。"""

    sequence: int
    tool_name: str
    arguments: dict[str, Any]
    status: str          # "success" | "failed"
    result_preview: str = ""
    error_message: str = ""
    # 被预算拦下、根本没跑的调用用 None —— 它没有耗时可言，
    # 填 0 的话前端会显示"0.0 秒"，看起来像跑了但瞬间完成。
    duration_ms: int | None = 0
    tool_call_id: str = ""


@dataclass
class ToolExecutionCollector:
    """收集一次 AgentRun 中的所有工具执行记录。

    AgentService 每跑一次 Agent 就 new 一个，传给中间件，
    Agent 结束后从里面读出所有记录，批量写进 ToolCall 表。
    """

    # 所有记录，**包含被预算拦下、根本没跑的那些**（写 ToolCall 表用）
    records: list[ToolExecutionRecord] = field(default_factory=list)
    # **真正跑过**的 tool_call id，用来判断哪些调用被预算拦截了（见 find_blocked_tool_calls）
    executed_call_ids: set[str] = field(default_factory=set)
    # 其中属于"被拦下"的条数
    blocked_count: int = 0

    @property
    def count(self) -> int:
        """**真正执行过**的工具调用次数。

        故意不包含被拦下的调用：这个数字会写进 AgentRun.tool_call_count 和
        Article.tool_call_count，用户看到的"工具调用次数"应该是真实发生的次数。
        """
        return len(self.records) - self.blocked_count

    @property
    def total(self) -> int:
        """记录总条数（含被拦下的）。只用来编排序号。"""
        return len(self.records)

    def add(self, record: ToolExecutionRecord, *, executed: bool = True) -> None:
        """记一条工具调用记录。

        executed=False 用于"被预算拦下、根本没跑"的调用：
        它要出现在时间线上（用户能看到研究是在哪一步被截断的），
        但**不能**算进 count，也**不能**进 executed_call_ids ——
        否则 find_blocked_tool_calls 会以为它执行过了，
        风险提示里那句"达到调用上限"就再也不会出现。
        """
        self.records.append(record)
        if executed:
            if record.tool_call_id:
                self.executed_call_ids.add(record.tool_call_id)
        else:
            self.blocked_count += 1


class ToolAuditMiddleware(AgentMiddleware):
    """记录工具调用的中间件。

    用法：
        collector = ToolExecutionCollector()
        agent = create_agent(..., middleware=[ToolAuditMiddleware(collector), ...])
    """

    def __init__(self, collector: ToolExecutionCollector) -> None:
        super().__init__()
        self.collector = collector

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """工具执行前后的拦截点。"""
        tool_call = request.tool_call or {}
        tool_name = tool_call.get("name", "unknown")
        arguments = tool_call.get("args") or {}
        tool_call_id = tool_call.get("id", "")

        start = time.monotonic()
        try:
            result = handler(request)
        except Exception as exc:
            # 工具抛异常了：记录下来，然后**继续往上抛**，
            # 交给 LangChain 的 ToolErrorMiddleware / Agent 循环去处理。
            duration_ms = int((time.monotonic() - start) * 1000)
            safe_message = _safe_error_text(exc)
            self.collector.add(
                ToolExecutionRecord(
                    sequence=self.collector.total + 1,
                    tool_name=tool_name,
                    arguments=_safe_arguments(arguments),
                    status="failed",
                    error_message=safe_message,
                    duration_ms=duration_ms,
                    tool_call_id=tool_call_id,
                )
            )
            logger.warning("tool %s failed duration_ms=%s error=%s", tool_name, duration_ms, safe_message)
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        preview, ok = _preview_of(result)

        self.collector.add(
            ToolExecutionRecord(
                sequence=self.collector.total + 1,
                tool_name=tool_name,
                arguments=_safe_arguments(arguments),
                status="success" if ok else "failed",
                result_preview=preview,
                duration_ms=duration_ms,
                tool_call_id=tool_call_id,
            )
        )
        logger.info("tool %s completed duration_ms=%s ok=%s", tool_name, duration_ms, ok)
        return result


def find_blocked_tool_calls(messages: list, collector: ToolExecutionCollector) -> list[str]:
    """找出"模型申请调用、但其实没有执行"的工具 —— 也就是被限流拦下的那些。

    原理：遍历所有 AIMessage 里的 tool_calls，凡是不在
    collector.executed_call_ids 里的，就是被中间件拦掉、没真正跑起来的。

    返回被拦截的工具名列表。
    """
    blocked: list[str] = []
    for message in messages or []:
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id", "")
            name = call.get("name", "unknown")
            # 结构化输出工具是正常收尾，不算被拦截
            if name == "ArticleDraft":
                continue
            if call_id and call_id not in collector.executed_call_ids:
                blocked.append(name)
    return blocked


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _preview_of(result: Any) -> tuple[str, bool]:
    """从工具返回值里提取"给人看的预览"和"是否成功"。

    工具统一返回 dict，成功时带 success=True，失败时带 success=False。
    为了防止把整页网页正文写进数据库，这里一律截断到 500 字符。
    """
    from django.conf import settings

    max_chars = settings.AGENT_LIMITS["RESULT_PREVIEW_CHARS"]

    data: Any = result
    # LangChain 的工具返回值可能是 ToolMessage，取它的 content
    if hasattr(result, "content") and not isinstance(result, dict):
        data = result.content

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON，就当作纯文本处理
            return _truncate(data, max_chars), True

    if not isinstance(data, dict):
        return _truncate(str(data), max_chars), True

    ok = bool(data.get("success", True))

    # 成功时给一段人类可读的摘要，而不是把整个结果塞进去
    if ok:
        if data.get("results") is not None:  # web_search
            titles = [r.get("title", "") for r in (data.get("results") or [])[:5]]
            preview = f"返回 {data.get('count', len(titles))} 条结果：" + "；".join(t for t in titles if t)
        elif data.get("text") is not None:   # read_webpage
            preview = (
                f"读取《{data.get('title', '')}》，"
                f"{data.get('word_count', 0)} 字"
                + ("（已截断）" if data.get("truncated") else "")
            )
        else:
            preview = json.dumps(data, ensure_ascii=False)
    else:
        preview = f"失败：{data.get('error', '')} {data.get('message', '')}".strip()

    return _truncate(preview, max_chars), ok


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[:max_chars] + "...(已截断)"


def _safe_arguments(arguments: dict) -> dict:
    """确保参数可以被 JSONField 序列化。

    模型偶尔会传进来一些奇怪的对象，直接入库会报错。
    """
    if not isinstance(arguments, dict):
        return {"_raw": str(arguments)[:500]}

    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, dict)):
            try:
                json.dumps(value, ensure_ascii=False)
                safe[key] = value
            except (TypeError, ValueError):
                safe[key] = str(value)[:500]
        else:
            safe[key] = str(value)[:500]
    return safe


def _safe_error_text(exc: Exception) -> str:
    """把异常转成安全的、可入库的文本（不包含敏感信息）。

    只取异常类型和消息，绝不带 Traceback。
    """
    message = str(exc) or ""
    # 保险起见，把可能出现的 key 形态遮掉
    for marker in ("sk-", "Bearer "):
        if marker in message:
            message = "（错误信息中包含敏感内容，已隐藏）"
            break
    return f"{type(exc).__name__}: {message}"[:500]
