"""
研究预算中间件 —— 让 Agent 在"研究够了"的时候收手，但仍然把文章写出来。

为什么不能直接用 LangChain 自带的 ToolCallLimitMiddleware？
    它只有三种退出行为，没有一种是我们想要的：

        exit_behavior="error"    直接抛异常          -> 整次生成失败
        exit_behavior="end"      立刻结束循环        -> 没有文章
        exit_behavior="continue" 拦住工具，让模型继续 -> ★ 实测会出问题

    实测"continue"的实际表现（真实模型，DeepSeek）：
        模型请求调用 read_webpage
        -> 中间件注入 "Tool call limit exceeded. Do not make additional tool calls."
        -> 模型把这句话理解成"那我什么都不做了"
        -> 返回一条空消息，循环结束
        -> structured_response 是 None
        -> 整次生成判定失败，用户什么也拿不到

    问题出在：对模型来说"返回文章"也**是一次工具调用**（结构化输出工具）。
    它收到"不许再调用工具"后，连出稿这一步也不敢做了。

我们想要的其实是：
    检索到上限就停，但**文章必须产出**，并把"研究不充分"如实写进风险提示。

这个中间件怎么做到？
    关键机制：LangChain 的 wrap_tool_call 钩子**只会被真实工具触发**
    （web_search / read_webpage），**不会**被结构化输出工具触发。
    所以只要在这里拦，就天然实现了"拦住检索、放行出稿"。

    而且我们不是简单地说"不许调用"，而是明确告诉模型**下一步该做什么** ——
    实测这个区别就是"生成成功"和"整次失败"的区别。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from apps.agent.agents.audit_middleware import ToolExecutionCollector, ToolExecutionRecord

logger = logging.getLogger(__name__)

# 拦截时回给模型的话。措辞是调过的：
#   1. 先说清楚"为什么"（到上限了），模型才不会以为是自己做错了；
#   2. 再明确说"现在必须做什么"（直接出稿），这一句是关键；
#   3. 最后说"资料缺口要写进风险提示"，保证用户知道这篇是在什么条件下写的。
BUDGET_EXCEEDED_MESSAGE = (
    "【系统提示】本次研究已达到工具调用上限（{limit} 次），"
    "系统将不再执行任何新的搜索或网页读取请求。\n\n"
    "这不是出错，是正常的预算控制。请立即停止调用检索类工具，"
    "**基于你目前已经获得的全部资料，直接输出最终文章**。\n\n"
    "同时请在风险提示（risk_notes）中如实说明：本次研究因达到调用上限而中断，"
    "部分信息可能不完整，建议用户自行核实。"
)


class ToolBudgetMiddleware(AgentMiddleware):
    """限制真实工具的调用次数，但始终允许模型产出文章。

    用法（预算靠 ToolAuditMiddleware 统计调用次数，两者必须配对使用）：
        collector = ToolExecutionCollector()
        middleware=[ToolBudgetMiddleware(collector, limit=8), ToolAuditMiddleware(collector)]

    ★ 顺序不能反：budget 必须在外层。反过来的话，audit 会把 budget 返回的
      那条"已达到上限"的 ToolMessage 当成一次**成功的工具调用**再记一遍，
      实测 tool_call_count 会从 1 变成 3。
    """

    def __init__(self, collector: ToolExecutionCollector, limit: int) -> None:
        super().__init__()
        self.collector = collector
        # 允许配 0：意思是"这次完全不做外部研究"，第一次检索就会被拦下，
        # 模型会基于已有知识写作并把资料受限写进风险提示。
        # 负数没有意义，夹到 0。
        self.limit = max(0, int(limit))
        self.blocked_count = 0

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """工具执行前的预算检查。

        返回 ToolMessage 就表示"这次调用被我们接管了"，工具不会真的执行。
        注意必须带上 tool_call_id —— 否则消息历史里 AIMessage 的
        tool_calls 和 ToolMessage 对不上，LangGraph 会直接报错。
        """
        if self.collector.count < self.limit:
            return handler(request)

        tool_call = request.tool_call or {}
        tool_name = tool_call.get("name", "unknown")
        tool_call_id = tool_call.get("id", "")

        self.blocked_count += 1

        # 被拦下的调用也记一条（executed=False），前端时间线上能看到
        # "研究是在哪一步被截断的"，而不是莫名其妙地少了一步。
        # 注意必须传 executed=False，否则 find_blocked_tool_calls 会认不出它。
        self.collector.add(
            ToolExecutionRecord(
                sequence=self.collector.total + 1,
                tool_name=tool_name,
                arguments=request.tool_call.get("args") or {},
                # 用 blocked 而不是 failed：它根本没有执行，
                # 记成 failed 会让"研究过程中有 N 次工具调用失败"这条风险提示误报
                status="blocked",
                error_message=f"已达到工具调用上限（{self.limit} 次），该请求未执行。",
                duration_ms=None,
                tool_call_id=tool_call_id,
            ),
            executed=False,
        )

        logger.info(
            "工具调用被预算拦截 tool=%s 已用=%s 上限=%s",
            tool_name,
            self.collector.count,
            self.limit,
        )

        return ToolMessage(
            content=BUDGET_EXCEEDED_MESSAGE.format(limit=self.limit),
            tool_call_id=tool_call_id,
            name=tool_name,
        )
