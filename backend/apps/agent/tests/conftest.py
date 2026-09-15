"""
Agent 测试共用的 fixture。

放在 conftest.py 里，pytest 会自动发现，同一个目录下的所有测试文件都能直接用，
不需要 import。

核心思路：**让测试既真实又免费**。
    真实的 Agent 循环（LangChain 中间件、工具调用、来源登记、数据库写入）全部照跑，
    只把两个"要花钱 / 要联网"的东西换成假的：
        1. 大模型  -> ScriptedChatModel（按剧本演戏）
        2. 搜索 API -> 本地假数据
"""

from __future__ import annotations

import socket
from importlib import import_module

import httpx
import pytest

from apps.agent.tests.fakes import ScriptedChatModel
from apps.agent.tools.web_search import SearchResponse, SearchResult

# ---------------------------------------------------------------------------
# 假数据
# ---------------------------------------------------------------------------
# 搜索工具会"搜到"这两条。测试里凡是引用它们 URL 的，都算真实来源。
SEARCH_RESULTS = [
    SearchResult(
        title="2026 年 AI Agent 行业报告",
        url="https://real-report.com/2026-ai-agent",
        snippet="报告显示，具备工具调用能力的智能体正在进入生产环境。",
    ),
    SearchResult(
        title="主流媒体的相关报道",
        url="https://real-news.com/ai-agent-2026",
        snippet="多家企业开始把智能体接入实际业务流程。",
    ),
]

# read_webpage "读回来"的页面。故意放了几段正文，让提取逻辑有东西可提。
WEBPAGE_HTML = """
<html><head><title>2026 年 AI Agent 行业报告</title></head>
<body>
  <nav>首页 | 关于我们</nav>
  <article>
    <h1>2026 年 AI Agent 行业报告</h1>
    <p>报告显示，具备工具调用能力的智能体正在大规模进入生产环境，这是一段足够长的正文。</p>
    <p>与此同时，可靠性与成本仍然是阻碍落地的两个主要因素，行业仍在摸索最佳实践。</p>
    <p>受访企业普遍表示，他们更关心的是稳定性，而不是模型在榜单上的名次。</p>
    <p>下一阶段的竞争焦点，很可能会转移到评测体系和工程化能力上来。</p>
  </article>
  <footer>版权所有</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_search(mocker):
    """把搜索服务换成本地假数据，测试不联网。"""
    provider = mocker.Mock()
    provider.name = "tavily"
    provider.search.return_value = SearchResponse(results=list(SEARCH_RESULTS))
    mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)
    return provider


@pytest.fixture
def fake_webpage(mocker):
    """把网页抓取换成本地假 HTML，并把 DNS 解析指向一个公网 IP。"""
    # 注意：必须用 import_module 拿模块对象本身。
    # apps/agent/tools/__init__.py 里 `from ... import read_webpage` 已经把包属性
    # 覆盖成"工具对象"了，直接 from ... import 会拿到 StructuredTool。
    module = import_module("apps.agent.tools.read_webpage")
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, html=WEBPAGE_HTML, headers={"content-type": "text/html; charset=utf-8"}
        )

    mocker.patch.object(
        module.httpx,
        "Client",
        side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    mocker.patch(
        "apps.agent.tools.url_safety.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    return handler


@pytest.fixture(autouse=True)
def _block_real_llm(mocker):
    """安全网：默认让"创建真实模型"这件事直接报错。

    为什么需要它？
        曾经有一个测试忘了用 patch_model，结果真的联网去调了付费大模型，
        不但慢（跑了几十秒），还会产生费用，而且结果是不可控的。
        有了这层保护，忘记打桩的测试会**立刻失败并给出明确提示**，
        而不是偷偷花钱。

    注意：patch_model 会在它之后再 patch 一次，把这里覆盖掉，
    所以正常使用假模型的测试不受影响。
    """

    def _explode(*args, **kwargs):
        raise AssertionError(
            "这个测试没有提供假模型，却尝试创建真实模型。"
            "请在这个测试里加上 patch_model fixture，不要真的调用付费大模型。"
        )

    mocker.patch("apps.agent.agents.content_agent.get_chat_model", side_effect=_explode)


@pytest.fixture
def patch_model(mocker):
    """返回一个函数：传入"剧本"，把 Agent 用的模型换成假模型。

    用法：
        patch_model([
            make_tool_call_message("web_search", {"query": "..."}),
            make_article_draft_message(sample_draft_args()),
        ])
    """

    def _patch(responses: list) -> ScriptedChatModel:
        model = ScriptedChatModel(responses=responses)
        mocker.patch("apps.agent.agents.content_agent.get_chat_model", return_value=model)
        return model

    return _patch


def request_data(**overrides) -> dict:
    """一份合法的生成请求参数，测试里改其中几个字段即可。"""
    data = {
        "topic": "2026 年 AI Agent 有哪些新进展",
        "audience": "普通互联网用户",
        "goal": "知识科普",
        "tone": "自然、有观点",
        "length": "medium",
        "research_mode": True,
        "extra_requirements": "",
    }
    data.update(overrides)
    return data
