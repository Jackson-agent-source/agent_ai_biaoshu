"""
两个核心工具的测试。

原则：
1. 全部离线。不真的调搜索 API，也不真的抓网页 —— 否则测试会因为网络波动而失败。
2. 重点测"失败路径"。工具失败时不能让 Agent 崩掉，这是产品要求（场景 E）。
"""

from __future__ import annotations

import socket
from importlib import import_module

import httpx
import pytest
from django.test import override_settings

from apps.agent.tools.read_webpage import read_webpage
from apps.agent.tools.web_search import (
    GoogleSearchProvider,
    NullSearchProvider,
    SearchResponse,
    SearchResult,
    TavilySearchProvider,
    get_search_provider,
    web_search,
)

# 注意：必须用 import_module 拿到"模块对象"本身。
# 因为 apps/agent/tools/__init__.py 里做了 `from ... import read_webpage`，
# 包属性 read_webpage 已经被"工具对象"覆盖了，直接写
# `from apps.agent.tools import read_webpage as module` 会拿到工具而不是模块。
read_webpage_module = import_module("apps.agent.tools.read_webpage")
web_search_module = import_module("apps.agent.tools.web_search")


# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_dns(mocker):
    """把所有域名解析都指向一个公网 IP。

    这样测试就不会依赖真实 DNS，也不会因为我们没联网而失败。
    """
    fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)


def _mock_httpx_client(mocker, handler):
    """把 read_webpage 内部用的 httpx.Client 换成"按剧本应答"的假客户端。

    这样我们就能模拟各种服务器行为：正常返回、超时、巨大响应体、重定向……
    而且完全不需要联网。
    """
    real_client = httpx.Client

    def factory(**kwargs):
        # 忽略真实代码传进来的 transport（如果有），换成我们的 mock
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    return mocker.patch.object(read_webpage_module.httpx, "Client", side_effect=factory)


# ===========================================================================
# Tool 1: web_search
# ===========================================================================
class TestWebSearch:
    """web_search 工具。"""

    def test_web_search_success(self, mocker):
        """成功搜索：返回结构化结果，每条都登记进 SourceRegistry。"""
        from apps.agent.services.source_registry import SourceRegistry, activate, deactivate

        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(
            results=[
                SearchResult(
                    title="AI Agent 年度报告",
                    url="https://example.com/report",
                    snippet="2026 年 AI Agent 市场规模达到……",
                ),
                SearchResult(
                    title="另一篇报道",
                    url="https://news.example.org/story",
                    snippet="行业观察……",
                ),
            ]
        )
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        registry = SourceRegistry()
        token = activate(registry)
        try:
            result = web_search.invoke({"query": "2026 AI Agent 发展", "max_results": 5})
        finally:
            deactivate(token)

        # 返回结构必须符合约定
        assert result["success"] is True
        assert result["count"] == 2
        assert result["query"] == "2026 AI Agent 发展"
        assert result["results"][0]["title"] == "AI Agent 年度报告"
        assert result["results"][0]["url"] == "https://example.com/report"
        assert result["results"][0]["domain"] == "example.com"

        # ★ 关键：搜到的 URL 必须进入登记簿，否则后面来源校验会把它们当成假的
        assert registry.contains("https://example.com/report")
        assert registry.contains("https://news.example.org/story")
        assert len(registry) == 2

    def test_web_search_failure(self, mocker):
        """搜索服务失败时：返回 success=False，**不抛异常**。

        这是需求文档场景 E 的要求 —— 搜索挂了整个服务不能崩。
        """
        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(
            error="search_provider_timeout", message="搜索服务响应超时。"
        )
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        result = web_search.invoke({"query": "test"})

        assert result["success"] is False
        assert result["error"] == "search_provider_timeout"
        assert "超时" in result["message"]

    def test_web_search_provider_not_configured(self):
        """没配 SEARCH_API_KEY 时，返回明确提示而不是崩溃。"""
        with override_settings(SEARCH_PROVIDER="", SEARCH_API_KEY=""):
            result = web_search.invoke({"query": "test"})

        assert result["success"] is False
        assert result["error"] == "search_provider_not_configured"
        # 提示里要告诉模型"该怎么办"
        assert "risk_notes" in result["message"]

    def test_web_search_empty_query(self):
        """空关键词直接返回错误，不浪费一次 API 调用。"""
        result = web_search.invoke({"query": "   "})
        assert result["success"] is False
        assert result["error"] == "empty_query"

    def test_web_search_result_limit(self, mocker):
        """结果条数必须被夹紧：请求 100 条也最多给 MAX_SEARCH_RESULTS 条。"""
        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(results=[])
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        with override_settings(AGENT_LIMITS={**__import__("django.conf", fromlist=["settings"]).settings.AGENT_LIMITS, "MAX_SEARCH_RESULTS": 5}):
            web_search.invoke({"query": "test", "max_results": 100})

        # 传给 provider 的 max_results 应该被夹到 5
        _args, _kwargs = provider.search.call_args
        assert _args[1] == 5

    def test_web_search_result_limit_hard_cap(self, mocker):
        """即使有人把配置改成 50，硬上限 10 依然生效。"""
        from django.conf import settings as dj_settings

        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(results=[])
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        with override_settings(
            AGENT_LIMITS={**dj_settings.AGENT_LIMITS, "MAX_SEARCH_RESULTS": 50}
        ):
            web_search.invoke({"query": "test", "max_results": 50})

        assert provider.search.call_args[0][1] == 10

    def test_result_snippet_is_truncated(self, mocker):
        """超长摘要要截断，避免入库和进上下文的内容过大。"""
        from apps.agent.services.source_registry import SourceRegistry, activate, deactivate

        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(
            results=[
                SearchResult(title="长文", url="https://example.com/long", snippet="啊" * 5000),
            ]
        )
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        registry = SourceRegistry()
        token = activate(registry)
        try:
            web_search.invoke({"query": "test"})
        finally:
            deactivate(token)

        assert len(registry.all()[0].snippet) < 1000


class TestTavilyProvider:
    """Tavily Provider 自身的错误处理。"""

    def test_timeout_returns_error_not_raises(self, mocker):
        """搜索超时：返回机器可读的错误码，不抛异常。"""
        provider = TavilySearchProvider(api_key="fake-key", timeout=1)
        mocker.patch.object(
            web_search_module.httpx,
            "Client",
            mocker.Mock(side_effect=httpx.TimeoutException("timed out")),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_timeout"
        assert response.results == []

    def test_401_returns_unauthorized(self, mocker):
        provider = TavilySearchProvider(api_key="bad-key", timeout=1)

        def handler(request):
            return httpx.Response(401, json={"detail": "invalid api key"})

        real_client = httpx.Client
        mocker.patch(
            "apps.agent.tools.web_search.httpx.Client",
            side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

        response = provider.search("test", 5)
        assert response.error == "search_provider_unauthorized"


# ---------------------------------------------------------------------------
# Google Programmable Search
# ---------------------------------------------------------------------------
def _google_response(items=None, status=200, body=None):
    """一份 Google Custom Search JSON API 的真实返回形状。"""
    if body is not None:
        return httpx.Response(status, json=body)
    return httpx.Response(status, json={"items": items or []})


GOOGLE_SAMPLE_ITEMS = [
    {
        "title": "2026 AI Agent Enterprise Adoption Report",
        "link": "https://example.com/ai-agent-report-2026",
        "snippet": "Survey of 1,200 enterprises shows 11% running agents in production.",
        "pagemap": {
            "metatags": [
                {
                    "og:title": "2026 AI Agent Enterprise Adoption Report",
                    "article:published_time": "2026-06-03T09:00:00Z",
                }
            ]
        },
    },
    {
        "title": "Why agentic AI projects get cancelled",
        "link": "https://example.org/agentic-cancellations",
        "snippet": "Gartner predicts over 40% of agentic AI projects will be scrapped.",
    },
]


class TestGoogleProvider:
    """Google Provider —— 换搜索后端时最要紧的是别把错误信息搞错。"""

    def _provider(self, mocker, handler, **kwargs):
        real_client = httpx.Client
        mocker.patch(
            "apps.agent.tools.web_search.httpx.Client",
            side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )
        defaults = {"api_key": "fake-key", "engine_id": "fake-cx", "timeout": 1}
        defaults.update(kwargs)
        return GoogleSearchProvider(**defaults)

    def test_parses_google_items(self, mocker):
        """正常返回：标题、链接、摘要都要拿到，发布日期能从 pagemap 里捞出来。"""
        provider = self._provider(mocker, lambda r: _google_response(GOOGLE_SAMPLE_ITEMS))

        response = provider.search("AI agent adoption 2026", 5)

        assert response.error == ""
        assert len(response.results) == 2
        first = response.results[0]
        assert first.title == "2026 AI Agent Enterprise Adoption Report"
        assert first.url == "https://example.com/ai-agent-report-2026"
        assert "11%" in first.snippet
        assert first.source == "google"
        # article:published_time 被解析出来了
        assert first.published_at is not None
        assert first.published_at.year == 2026

    def test_sends_english_bias_and_language_restriction(self, mocker):
        """请求参数要对：hl=en 让排序偏英文，lr 只有在配了 SEARCH_LANGUAGE 时才发。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return _google_response(GOOGLE_SAMPLE_ITEMS)

        provider = self._provider(mocker, handler, language="lang_en")
        provider.search("test query", 5)

        assert seen["params"]["q"] == "test query"
        assert seen["params"]["hl"] == "en"
        assert seen["params"]["lr"] == "lang_en"
        assert seen["params"]["cx"] == "fake-cx"
        assert seen["params"]["num"] == "5"

    def test_language_not_sent_when_empty(self, mocker):
        """SEARCH_LANGUAGE 留空时不该发 lr —— 否则中文补充查询就废了。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return _google_response(GOOGLE_SAMPLE_ITEMS)

        provider = self._provider(mocker, handler, language="")
        provider.search("test", 5)

        assert "lr" not in seen["params"]

    def test_num_is_capped_at_ten(self, mocker):
        """Google 的 num 上限就是 10，模型要 50 条也只能给 10。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return _google_response(GOOGLE_SAMPLE_ITEMS)

        provider = self._provider(mocker, handler)
        provider.search("test", 50)

        assert seen["params"]["num"] == "10"

    def test_quota_exceeded_says_what_to_do(self, mocker):
        """★ 免费额度用完（100 次/天）是最常见的失败，提示必须能指导行动。"""
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=403,
                body={
                    "error": {
                        "code": 403,
                        "message": "Daily Limit Exceeded",
                        "errors": [{"reason": "dailyLimitExceeded"}],
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_quota_exceeded"
        assert "100" in response.message
        assert "额度" in response.message

    def test_bad_engine_id_mentions_the_real_cause(self, mocker):
        """★ cx 建错（没选「搜索整个网络」）会报 400，提示要指向 SEARCH_ENGINE_ID。"""
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=400,
                body={
                    "error": {
                        "code": 400,
                        "message": "Invalid Value",
                        "errors": [{"reason": "invalidArgument"}],
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_bad_engine"
        assert "SEARCH_ENGINE_ID" in response.message

    def test_no_entitlement_tells_user_to_give_up_on_google(self, mocker):
        """★ 实测遇到的最终答案：项目根本没有这个 API 的授权。

        注意它的 reason 同样是 forbidden，很容易被误判成「Key 限制」，
        于是把用户引到改 API 限制的死路上绕圈 —— 所以这条必须优先匹配。
        提示要明确说"改配置没用"，而不是继续给操作步骤。
        """
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=403,
                body={
                    "error": {
                        "code": 403,
                        "message": "This project does not have the access to Custom Search JSON API.",
                        "errors": [{"reason": "forbidden"}],
                        "status": "PERMISSION_DENIED",
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_no_entitlement"
        assert "新项目" in response.message
        # 不能再把用户引到"去 Credentials 页面改配置"那条死路上
        # （注意别断言 "API 限制" 这个词本身 —— 提示语里会把它当成"改了也没用"的例子提到）
        assert "credentials" not in response.message.lower()
        assert "勾上" not in response.message

    def test_api_restriction_blocked_gives_actionable_hint(self, mocker):
        """★ 实测踩到的坑：Key 本身有效，但它的「API 限制」清单里没有 Custom Search。

        Google 的原话是 "Requests to this API customsearch method
        ... are blocked."，reason 是 forbidden。
        这一条必须和「Key 无效」区分开 —— 否则用户会跑去重新申请 Key，白折腾。
        """
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=403,
                body={
                    "error": {
                        "code": 403,
                        "message": (
                            "Requests to this API customsearch method "
                            "google.customsearch.v1.CustomSearchService.List are blocked."
                        ),
                        "errors": [{"reason": "forbidden"}],
                        "status": "PERMISSION_DENIED",
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_api_restricted"
        assert "API 限制" in response.message
        assert "credentials" in response.message

    @pytest.mark.parametrize("reason", ["accessNotConfigured", "SERVICE_DISABLED"])
    def test_api_not_enabled_says_enable_the_api(self, mocker, reason):
        """另一种 403：API 压根没在项目里启用。提示要指向「启用」而不是「改限制」。

        ★ 这个 case 必须两种 reason 都测：Google 实测返回的是
        accessNotConfigured，而文档里常见的是 SERVICE_DISABLED。
        只按其中一个判断，另一个就会被漏判成"Key 无效"。
        """
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=403,
                body={
                    "error": {
                        "code": 403,
                        "message": "Custom Search API has not been used in project 123 before or it is disabled.",
                        "errors": [{"reason": reason}],
                        "status": "PERMISSION_DENIED",
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_api_not_enabled"
        assert "启用" in response.message

    def test_invalid_api_key_on_400(self, mocker):
        """Key 填错时 Google 给的是 400 + "API key not valid"，不是 403。"""
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                status=400,
                body={
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "errors": [{"reason": "badRequest"}],
                        "status": "INVALID_ARGUMENT",
                    }
                },
            ),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_unauthorized"
        assert "SEARCH_API_KEY" in response.message

    def test_error_message_never_leaks_api_key(self, mocker):
        """★ 报错信息绝对不能带出 API Key —— 它会显示给前端。"""
        provider = self._provider(
            mocker,
            lambda r: _google_response(status=500, body={"error": {"message": "boom"}}),
        )

        response = provider.search("test", 5)

        assert "fake-key" not in response.message
        assert "fake-key" not in response.error

    def test_timeout_returns_error_not_raises(self, mocker):
        """超时同样只返回错误码，不能把异常抛给 Agent。"""
        provider = GoogleSearchProvider(api_key="fake-key", engine_id="fake-cx", timeout=1)
        mocker.patch.object(
            web_search_module.httpx,
            "Client",
            mocker.Mock(side_effect=httpx.TimeoutException("timed out")),
        )

        response = provider.search("test", 5)

        assert response.error == "search_provider_timeout"
        assert response.results == []

    def test_skips_items_without_link(self, mocker):
        """Google 偶尔会返回没有 link 的条目，跳过它们而不是崩掉。"""
        provider = self._provider(
            mocker,
            lambda r: _google_response(
                [{"title": "没有链接的条目"}, *GOOGLE_SAMPLE_ITEMS]
            ),
        )

        response = provider.search("test", 5)

        assert len(response.results) == 2
        assert all(item.url for item in response.results)


class TestSearchProviderSelection:
    """get_search_provider 的分支选择 —— 配错了要给出能照着做的提示。"""

    def test_google_without_engine_id_explains_what_is_missing(self):
        """★ 选了 google 却没填 cx：说清楚缺什么，而不是笼统的"未配置"。"""
        with override_settings(
            SEARCH_PROVIDER="google", SEARCH_API_KEY="fake-key", SEARCH_ENGINE_ID=""
        ):
            provider = get_search_provider()

        assert isinstance(provider, NullSearchProvider)
        response = provider.search("test", 5)
        assert "SEARCH_ENGINE_ID" in response.message
        assert "整个网络" in response.message

    def test_google_provider_is_selected_when_configured(self):
        with override_settings(
            SEARCH_PROVIDER="google",
            SEARCH_API_KEY="fake-key",
            SEARCH_ENGINE_ID="fake-cx",
            SEARCH_LANGUAGE="",
        ):
            provider = get_search_provider()

        assert isinstance(provider, GoogleSearchProvider)
        assert provider.engine_id == "fake-cx"

    def test_stale_base_url_from_another_provider_is_ignored(self):
        """★ 防呆：从 tavily 换成 google 却忘了清空 SEARCH_BASE_URL。

        不拦的话 Google 的请求会被打到 Tavily 的地址上，报一个莫名其妙的错。
        现在会忽略它并回落到默认地址。
        """
        with override_settings(
            SEARCH_PROVIDER="google",
            SEARCH_API_KEY="fake-key",
            SEARCH_ENGINE_ID="fake-cx",
            SEARCH_BASE_URL="https://api.tavily.com",
        ):
            provider = get_search_provider()

        assert isinstance(provider, GoogleSearchProvider)
        assert provider.base_url == "https://www.googleapis.com/customsearch/v1"

    def test_matching_base_url_is_respected(self):
        """域名对得上就照用 —— 比如 Google 的另一个入口域名，不能被防呆拦掉。"""
        with override_settings(
            SEARCH_PROVIDER="google",
            SEARCH_API_KEY="fake-key",
            SEARCH_ENGINE_ID="fake-cx",
            SEARCH_BASE_URL="https://customsearch.googleapis.com/customsearch/v1",
        ):
            provider = get_search_provider()

        assert provider.base_url == "https://customsearch.googleapis.com/customsearch/v1"

    def test_unknown_provider_falls_back_to_null(self):
        with override_settings(SEARCH_PROVIDER="bing", SEARCH_API_KEY="fake-key"):
            provider = get_search_provider()

        assert isinstance(provider, NullSearchProvider)


# ===========================================================================
# Tool 2: read_webpage
# ===========================================================================
SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>2026 年 AI Agent 行业观察</title></head>
<body>
  <nav>首页 | 关于我们 | 联系我们</nav>
  <script>console.log('这段不该出现在正文里');</script>
  <article>
    <h1>2026 年 AI Agent 行业观察</h1>
    <p>过去一年里，具备工具调用能力的智能体从演示走向了生产环境。</p>
    <p>多家机构的数据显示，企业在智能体上的投入明显增加。</p>
    <p>与此同时，可靠性和成本仍然是落地的主要障碍。</p>
    <p>业内人士认为，下一阶段的竞争焦点会转移到评测体系上。</p>
  </article>
  <footer>版权所有</footer>
</body>
</html>
"""


class TestReadWebpage:
    """read_webpage 工具。"""

    def test_read_webpage_invalid_scheme(self):
        """file:// 等协议必须被拒绝（不能读服务器本地文件）。"""
        result = read_webpage.invoke({"url": "file:///etc/passwd"})

        assert result["success"] is False
        assert result["error"] == "unsafe_url"

    def test_read_webpage_blocks_localhost(self):
        """localhost 必须被拒绝（防 SSRF）。"""
        result = read_webpage.invoke({"url": "http://localhost:8000/admin"})

        assert result["success"] is False
        assert result["error"] == "unsafe_url"

    def test_read_webpage_blocks_private_ip(self):
        """内网 IP 必须被拒绝。"""
        for url in ("http://192.168.1.1/", "http://10.0.0.5/", "http://169.254.169.254/"):
            result = read_webpage.invoke({"url": url})
            assert result["success"] is False, url
            assert result["error"] == "unsafe_url", url

    def test_read_webpage_empty_url(self):
        result = read_webpage.invoke({"url": ""})
        assert result["success"] is False
        assert result["error"] == "empty_url"

    def test_read_webpage_success(self, mocker):
        """正常读取：拿到标题和正文，导航栏/脚本被清掉。"""
        from apps.agent.services.source_registry import SourceRegistry, activate, deactivate

        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200,
                html=SAMPLE_ARTICLE_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        )

        registry = SourceRegistry()
        token = activate(registry)
        try:
            result = read_webpage.invoke({"url": "https://example.com/article"})
        finally:
            deactivate(token)

        assert result["success"] is True
        assert "AI Agent" in result["title"]
        assert "工具调用能力" in result["text"]
        # 导航栏和脚本内容不应该出现在正文里
        assert "关于我们" not in result["text"]
        assert "console.log" not in result["text"]
        assert result["word_count"] > 0
        assert result["truncated"] is False

        # 读过的网页要登记进来源，并且标记 was_read
        registered = registry.get("https://example.com/article")
        assert registered is not None
        assert registered.was_read is True

    def test_read_webpage_timeout(self, mocker):
        """超时：返回友好错误，不抛异常。"""
        _mock_httpx_client(mocker, lambda request: (_ for _ in ()).throw(httpx.TimeoutException("boom")))

        result = read_webpage.invoke({"url": "https://example.com/slow"})

        assert result["success"] is False
        assert result["error"] == "timeout"

    def test_read_webpage_http_error(self, mocker):
        """404 等 HTTP 错误：返回错误码，不抛异常。"""
        _mock_httpx_client(mocker, lambda request: httpx.Response(404, html="not found"))

        result = read_webpage.invoke({"url": "https://example.com/missing"})

        assert result["success"] is False
        assert result["error"] == "http_404"

    def test_read_webpage_rejects_binary_content(self, mocker):
        """图片/压缩包等非文本类型必须拒绝，不能下载。"""
        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200,
                content=b"PK\x03\x04binary-zip-content",
                headers={"content-type": "application/zip"},
            ),
        )

        result = read_webpage.invoke({"url": "https://example.com/file.zip"})

        assert result["success"] is False
        assert result["error"] == "unsupported_content"

    def test_read_webpage_size_limit(self, mocker):
        """响应体超过上限时截断，不能把内存吃爆。"""
        huge_html = "<html><body><article><p>" + ("这是一段很长很长的正文内容。" * 200_000) + "</p></article></body></html>"

        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200,
                html=huge_html,
                headers={"content-type": "text/html"},
            ),
        )

        result = read_webpage.invoke({"url": "https://example.com/huge"})

        assert result["success"] is True
        # 正文长度必须被 MAX_WEBPAGE_CHARS 限制住
        assert result["truncated"] is True
        assert len(result["text"]) <= 20_000

    def test_read_webpage_truncates_at_configured_limit(self, mocker):
        """把上限调到 200 字，验证截断确实按配置生效。"""
        from django.conf import settings as dj_settings

        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200, html=SAMPLE_ARTICLE_HTML, headers={"content-type": "text/html"}
            ),
        )

        with override_settings(
            AGENT_LIMITS={**dj_settings.AGENT_LIMITS, "MAX_WEBPAGE_CHARS": 50}
        ):
            result = read_webpage.invoke({"url": "https://example.com/article"})

        assert result["success"] is True
        assert result["truncated"] is True
        assert len(result["text"]) == 50

    def test_read_webpage_blocks_redirect_to_private_ip(self, mocker):
        """★ 关键安全测试：公网页面 302 跳到内网地址，必须拦下来。

        这是 SSRF 最常见的绕过手法 —— 只检查初始 URL 是不够的。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            # 如果真的跳过去了，这里会返回敏感数据 —— 但我们希望根本到不了这一步
            return httpx.Response(200, html="<html><body>SECRET METADATA</body></html>")

        _mock_httpx_client(mocker, handler)

        result = read_webpage.invoke({"url": "https://example.com/redirect"})

        assert result["success"] is False
        assert result["error"] == "unsafe_redirect"
        assert "SECRET" not in str(result)

    def test_read_webpage_follows_safe_redirect(self, mocker):
        """正常的公网跳转应该跟随。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "https://example.com/new"})
            return httpx.Response(
                200, html=SAMPLE_ARTICLE_HTML, headers={"content-type": "text/html"}
            )

        _mock_httpx_client(mocker, handler)

        result = read_webpage.invoke({"url": "https://example.com/old"})

        assert result["success"] is True
        assert result["url"] == "https://example.com/new"

    def test_read_webpage_too_many_redirects(self, mocker):
        """无限重定向要停下来。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://example.com/loop"})

        _mock_httpx_client(mocker, handler)

        result = read_webpage.invoke({"url": "https://example.com/loop"})

        assert result["success"] is False
        assert result["error"] in {"too_many_redirects", "request_failed"}

    def test_no_content_extracted(self, mocker):
        """页面是空的 / 全靠 JS 渲染：给出明确错误，而不是返回空字符串。"""
        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200, html="<html><body></body></html>", headers={"content-type": "text/html"}
            ),
        )

        result = read_webpage.invoke({"url": "https://example.com/empty"})

        assert result["success"] is False
        assert result["error"] == "no_content_extracted"

    def test_does_not_store_full_text_in_registry(self, mocker):
        """来源登记簿里只存摘要，不存网页全文。"""
        from apps.agent.services.source_registry import SourceRegistry, activate, deactivate

        # 造一个很长的网页，确保正文远超摘要上限
        long_html = (
            "<html><body><article><h1>长文标题</h1>"
            + "".join(f"<p>这是第 {i} 段正文内容，用来把页面撑长以便验证摘要截断。</p>" for i in range(300))
            + "</article></body></html>"
        )
        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200, html=long_html, headers={"content-type": "text/html"}
            ),
        )

        registry = SourceRegistry()
        token = activate(registry)
        try:
            result = read_webpage.invoke({"url": "https://example.com/long-article"})
        finally:
            deactivate(token)

        registered = registry.get("https://example.com/long-article")
        # 正文上万字，但登记簿里的摘要必须被截断
        assert len(registered.snippet) <= 600
        assert len(registered.snippet) < len(result["text"])

    def test_collapses_duplicated_body(self, mocker):
        """★ 回归测试：正文被整块重复时要合并成一份。

        trafilatura 在 precision 模式下对某些页面结构会把正文输出两遍。
        真实站点大多正常，但一旦触发，文章里就会出现成段重复的内容。
        """
        _mock_httpx_client(
            mocker,
            lambda request: httpx.Response(
                200, html=SAMPLE_ARTICLE_HTML, headers={"content-type": "text/html"}
            ),
        )

        result = read_webpage.invoke({"url": "https://example.com/article"})

        assert result["success"] is True
        text = result["text"]
        # 每一段只应出现一次
        for sentence in (
            "过去一年里，具备工具调用能力的智能体从演示走向了生产环境。",
            "业内人士认为，下一阶段的竞争焦点会转移到评测体系上。",
        ):
            assert text.count(sentence) == 1, f"这段内容重复了：{sentence}"
