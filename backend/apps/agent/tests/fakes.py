"""
测试用的假对象（Test Double）。

为什么要这些东西？
    真实跑一次 Agent 要花钱调大模型，而且要等几十秒，网络还可能失败。
    单元测试必须快、稳定、免费。所以测试里我们用"按剧本演戏"的假模型，
    它不会联网，只会把预先写好的回答依次吐出来。

用法见 test_agent_service.py。
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """按剧本返回响应的假聊天模型。

    参数 responses 是一个 AIMessage 列表，模型每被调用一次就吐出下一条。
    剧本用完之后会返回一条普通的结束消息，避免测试卡在死循环里。

    它实现了 bind_tools（直接返回自己），因为 create_agent 在绑定工具时会调用它。
    """

    responses: list[AIMessage] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    _index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-chat-model"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        """create_agent 需要给模型绑定工具。假模型不需要真的绑定，返回自己即可。"""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 记录调用历史，方便测试断言"模型确实被调用了"
        self.calls.append(list(messages))

        if self._index < len(self.responses):
            message = self.responses[self._index]
            self._index += 1
        else:
            # 剧本演完了：返回一条不带 tool_calls 的消息，让 Agent 正常收尾
            message = AIMessage(content="（测试剧本已结束）")

        return ChatResult(generations=[ChatGeneration(message=message)])


def make_tool_call_message(name: str, args: dict, call_id: str = "") -> AIMessage:
    """构造一条"模型要求调用工具"的消息。"""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id or f"call_{name}"}],
    )


def make_article_draft_message(draft_args: dict, call_id: str = "call_structured") -> AIMessage:
    """构造一条"模型返回结构化文章"的消息。

    结构化输出的工具名就是 Pydantic 类名 ArticleDraft —— 这是 LangChain
    ToolStrategy 的规则（见 langchain.agents.structured_output._SchemaSpec）。
    """
    return AIMessage(
        content="",
        tool_calls=[{"name": "ArticleDraft", "args": draft_args, "id": call_id}],
    )


def sample_draft_args(**overrides: Any) -> dict:
    """一份合法的 ArticleDraft 参数，测试里可以直接改其中几个字段。"""
    args = {
        "title": "AI Agent 正在改变什么",
        "subtitle": "从工具调用说起",
        "summary": "这是一段用于测试的摘要，说明这篇文章大概讲了什么内容。",
        "hook": "先说一个具体的场景：你让 AI 帮你查资料，它自己打开了三个网页。",
        "outline": ["发生了什么变化", "普通人该怎么办"],
        "body_markdown": "## 发生了什么变化\n\nAI Agent 开始会自己用工具了。\n\n## 普通人该怎么办\n\n先学会提问。",
        "image_suggestions": ["开头配一张流程图"],
        "sources": [],
        "risk_notes": [],
    }
    args.update(overrides)
    return args
