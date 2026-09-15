"""
工具 1：web_search —— 联网搜索。

设计要点：
1. 抽象出 SearchProvider 协议。以后要换 Exa / Bing / Google，只需要新增一个
   Provider 类，工具本身和 Agent 完全不用改。
2. 工具**永远不抛异常**给 Agent。搜索失败时返回
   {"success": false, "error": "search_provider_timeout"}，
   让模型自己决定"改用其他办法"或者"基于现有信息写作并说明资料受限"。
   如果直接抛异常，整个 Agent 流程就崩了 —— 这不符合需求文档的场景 E。
3. 结果条数有硬上限（10 条），防止一次搜索把上下文撑爆。
4. 每拿到一条结果就登记进 SourceRegistry（防幻觉的来源清单）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from django.conf import settings
from langchain_core.tools import tool

from apps.agent.services.source_registry import register_source
from apps.common.utils import extract_domain, normalize_whitespace, truncate

logger = logging.getLogger(__name__)

# 单次搜索结果的硬上限。即使模型请求 50 条，我们也只给 10 条。
HARD_MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    """一条搜索结果。"""

    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None
    source: str = ""  # 搜索引擎名字，例如 "tavily"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "domain": extract_domain(self.url),
        }


@dataclass
class SearchResponse:
    """搜索的返回结果（Provider 层）。"""

    results: list[SearchResult] = field(default_factory=list)
    error: str = ""       # 非空表示失败，值是机器可读的错误码
    message: str = ""     # 给模型/用户看的中文说明


# ---------------------------------------------------------------------------
# Provider 抽象层
# ---------------------------------------------------------------------------
class SearchProvider(Protocol):
    """搜索服务提供方需要实现的接口。

    以后接入 Exa，只要写一个类实现这个 search 方法即可。
    """

    name: str

    def search(self, query: str, max_results: int) -> SearchResponse:
        ...


class TavilySearchProvider:
    """Tavily 搜索（V1 真正实现的唯一 Provider）。

    官网：https://tavily.com
    免费额度足够个人开发使用。API Key 填在 .env 的 SEARCH_API_KEY。
    """

    name = "tavily"
    default_base_url = "https://api.tavily.com"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 20) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout

    @property
    def request_url(self) -> str:
        """真正会被请求的完整地址。报错时把它带上，方便定位配置问题。

        注意：这里只有地址，**不包含 api_key**（key 走 Authorization 头），
        所以放进错误信息里是安全的。
        """
        return f"{self.base_url}/search"

    def search(self, query: str, max_results: int) -> SearchResponse:
        """调用 Tavily 的 /search 接口。"""
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",       # basic 更快更便宜，V1 够用
            "include_answer": False,       # 不要它生成的摘要，避免二手信息
            "include_raw_content": False,  # 不要网页全文，正文交给 read_webpage
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/search", json=payload, headers=headers)
        except httpx.TimeoutException:
            logger.warning("Tavily 搜索超时 query=%s", query)
            return SearchResponse(error="search_provider_timeout", message="搜索服务响应超时。")
        except httpx.RequestError as exc:
            logger.warning("Tavily 搜索网络错误: %s", exc)
            return SearchResponse(error="search_provider_unreachable", message="无法连接搜索服务。")

        if response.status_code == 401:
            logger.error("Tavily 返回 401，SEARCH_API_KEY 可能无效")
            return SearchResponse(error="search_provider_unauthorized", message="搜索服务密钥无效。")
        if response.status_code == 429:
            logger.warning("Tavily 返回 429，请求过于频繁")
            return SearchResponse(error="search_provider_rate_limited", message="搜索服务请求过于频繁。")
        if response.status_code >= 400:
            # 把实际请求的地址一起说出来，否则用户很难猜到是 SEARCH_BASE_URL 配错了。
            # 最常见的坑：把控制台网页地址 app.tavily.com 当成 API 地址填了进去，
            # 结果请求打到网页上，返回一个 404 的 HTML 页面。
            host = urlparse(self.base_url).netloc or self.base_url
            logger.error(
                "Tavily 返回异常状态码 %s（请求地址 %s）: %s",
                response.status_code,
                self.request_url,
                response.text[:200],
            )
            hint = ""
            if "app.tavily.com" in host:
                hint = "你填的 SEARCH_BASE_URL 是控制台网页地址，请改成 https://api.tavily.com 或直接留空。"
            return SearchResponse(
                error="search_provider_error",
                message=(
                    f"搜索服务返回错误（HTTP {response.status_code}，请求地址 {self.request_url}）。"
                    + hint
                ),
            )

        try:
            data = response.json()
        except Exception:
            logger.exception("Tavily 返回内容不是合法 JSON")
            return SearchResponse(error="search_provider_bad_response", message="搜索服务返回内容无法解析。")

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    title=normalize_whitespace(item.get("title")) or url,
                    url=url,
                    snippet=normalize_whitespace(item.get("content")),
                    published_at=_parse_date(item.get("published_date")),
                    source=self.name,
                )
            )
        return SearchResponse(results=results)


class GoogleSearchProvider:
    """Google Programmable Search Engine（Custom Search JSON API）。

    ★★ 预警：Google 正在下线这条路，**新项目不要再选它**。★★

    2026-01-20 起：
      · 新建的搜索引擎**不能再选「搜索整个网络」**，只能用「Sites to search」，
        最多填 50 个域名（Google 官方说明：support.google.com/programmable-search/answer/12397162）。
      · Custom Search JSON API 已不再接受新用户，2027-01-01 彻底关停。
      · 已有的整网引擎可以继续用，但也只到 2027-01-01。

    换句话说：现在新申请的 cx 只覆盖你手填的那几个站，当通用网搜工具用是行不通的。
    这个 Provider 保留下来，是给**已经持有存量整网引擎**的人用的。

    替代方案见 README「关于联网搜索」一节。

    申请步骤（两样都要，缺一不可）：
        1. API Key：https://console.cloud.google.com/apis/credentials
           启用 "Custom Search API" 后创建凭据。免费额度每天 100 次查询。
        2. 搜索引擎 ID（cx）：https://programmablesearchengine.google.com/controlpanel/create
           ★ 建的时候一定要在"搜索范围"里选择**搜索整个网络**。
             默认是"仅搜索指定网站"，那样建出来的 cx 只能搜到你填的那几个站，
             搜什么都搜不到，而且不会报错 —— 这是最容易踩的坑。

    配额：免费 100 次/天。超了会返回 403，我们把错误码翻译成人话。
    """

    name = "google"
    default_base_url = "https://www.googleapis.com/customsearch/v1"

    def __init__(
        self,
        api_key: str,
        engine_id: str,
        base_url: str = "",
        timeout: int = 20,
        language: str = "",
    ) -> None:
        self.api_key = api_key
        self.engine_id = engine_id
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        # 形如 "lang_en"。留空表示不限制语言。
        self.language = language

    @property
    def request_url(self) -> str:
        """真正会被请求的地址。报错时带上，方便定位。

        注意：api_key 在查询参数里，所以这里**绝不能**把完整 URL（含 key）打出去。
        所以只给出 base_url，不带参数。
        """
        return self.base_url

    def search(self, query: str, max_results: int) -> SearchResponse:
        """调用 Google Custom Search JSON API。"""
        # Google 的 num 上限就是 10，和我们的硬上限一致
        num = max(1, min(max_results, HARD_MAX_RESULTS))

        params: dict[str, Any] = {
            "key": self.api_key,
            "cx": self.engine_id,
            "q": query,
            "num": num,
            # hl = 界面语言。设成英文能让结果排序更偏向英文内容，
            # 但不会把中文结果排除掉（排除要用 lr，也就是 SEARCH_LANGUAGE）。
            "hl": "en",
            "safe": "off",
        }
        if self.language:
            params["lr"] = self.language

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self.base_url, params=params)
        except httpx.TimeoutException:
            logger.warning("Google 搜索超时 query=%s", query)
            return SearchResponse(error="search_provider_timeout", message="搜索服务响应超时。")
        except httpx.RequestError as exc:
            logger.warning("Google 搜索网络错误: %s", exc)
            return SearchResponse(error="search_provider_unreachable", message="无法连接搜索服务。")

        if response.status_code >= 400:
            return self._translate_error(response)

        try:
            data = response.json()
        except Exception:
            logger.exception("Google 返回内容不是合法 JSON")
            return SearchResponse(error="search_provider_bad_response", message="搜索服务返回内容无法解析。")

        results: list[SearchResult] = []
        for item in (data.get("items") or [])[:num]:
            url = (item.get("link") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    title=normalize_whitespace(item.get("title")) or url,
                    url=url,
                    snippet=normalize_whitespace(item.get("snippet")),
                    published_at=_parse_pagemap_date(item.get("pagemap")),
                    source=self.name,
                )
            )
        return SearchResponse(results=results)

    def _translate_error(self, response: httpx.Response) -> SearchResponse:
        """把 Google 的错误码翻译成用户能看懂、知道该怎么办的中文。"""
        # Google 的错误详情在 body 里，形状是 {"error": {"code":..., "errors":[{"reason": "..."}]}}
        reason = ""
        message = ""
        try:
            body = response.json()
            error = body.get("error") or {}
            message = error.get("message") or ""
            errors = error.get("errors") or []
            if errors:
                reason = errors[0].get("reason") or ""
        except Exception:
            message = response.text[:200]

        logger.error(
            "Google 搜索返回 %s reason=%s: %s",
            response.status_code,
            reason or "-",
            message[:200],
        )

        if response.status_code == 400:
            if reason == "invalidArgument":
                return SearchResponse(
                    error="search_provider_bad_engine",
                    message=(
                        "Google 说搜索引擎 ID（SEARCH_ENGINE_ID）无效。"
                        "请检查 .env 里的 SEARCH_ENGINE_ID 是否填对，"
                        "以及该搜索引擎是否已开启「搜索整个网络」。"
                    ),
                )
            # key 填错/被删掉时，Google 给的是 400 + "API key not valid"
            if "api key not valid" in message.lower():
                return SearchResponse(
                    error="search_provider_unauthorized",
                    message=(
                        "Google 说这个 API Key 无效。"
                        "请检查 .env 里的 SEARCH_API_KEY 有没有复制错、有没有被删除。"
                    ),
                )

        if response.status_code == 403:
            # ★ 403 有三种完全不同的原因，混成一句话用户根本不知道该改哪里。
            # 实测踩过的是第一种：Key 有效，但它的「API 限制」清单里没有 Custom Search。
            if reason in ("dailyLimitExceeded", "rateLimitExceeded", "quotaExceeded"):
                return SearchResponse(
                    error="search_provider_quota_exceeded",
                    message=(
                        "Google 搜索的每日免费额度（100 次）已经用完了。"
                        "可以等到第二天，或在 Google Cloud 控制台提高配额（超过后按次计费）。"
                    ),
                )
            # 「API 没在项目里启用」这一种，Google 用过不止一个 reason 字符串：
            # 实测见到的是 accessNotConfigured，文档里常见的是 SERVICE_DISABLED。
            # 所以这里按关键词判断，别死抠某一个值 —— 否则换个 reason 就漏判了。
            if (
                reason in ("SERVICE_DISABLED", "accessNotConfigured")
                or "has not been used in project" in message
                or "it is disabled" in message
            ):
                return SearchResponse(
                    error="search_provider_api_not_enabled",
                    message=(
                        "这个 Google Cloud 项目还没有启用 Custom Search API。"
                        "去 https://console.cloud.google.com/apis/library/customsearch.googleapis.com "
                        "点「启用」，等一两分钟再试。"
                    ),
                )
            # ★★ 死胡同：这个分支必须放在"Key 限制"前面判断。
            # 它的 reason 也是 forbidden，但含意完全不同 —— 是**项目根本没有这个 API 的授权**，
            # 属于 Google 从 2026 年起对新客户关闭该 API 的结果。
            # 实测确认过：API 已启用、Key 有效、cx 正确，依然是这条报错。
            # 用户端无从解决，所以别让他去改配置，直接劝退。
            if "does not have the access" in message:
                return SearchResponse(
                    error="search_provider_no_entitlement",
                    message=(
                        "Google 已经不再给新项目开放 Custom Search JSON API 了"
                        "（2026 年起对新客户关闭，2027-01-01 彻底停用）。"
                        "这是账号级别的限制，改 API 限制、重新申请 Key、新建项目都没有用。"
                        "请改用其它搜索服务，见 README 的「关于联网搜索」一节。"
                    ),
                )
            if reason == "forbidden" or "blocked" in message.lower():
                return SearchResponse(
                    error="search_provider_api_restricted",
                    message=(
                        "这个 API Key 的「API 限制」清单里没有 Custom Search API，"
                        "所以 Google 拒绝用它调搜索接口。"
                        "去 https://console.cloud.google.com/apis/credentials 点开这个 Key，"
                        "在「API 限制」里把 Custom Search API 勾上"
                        "（或者直接选「不限制密钥」）。"
                    ),
                )
            return SearchResponse(
                error="search_provider_unauthorized",
                message="Google 拒绝了这次请求，SEARCH_API_KEY 可能无效，或 Custom Search API 没有启用。",
            )
        if response.status_code == 429:
            return SearchResponse(
                error="search_provider_rate_limited", message="搜索服务请求过于频繁。"
            )
        return SearchResponse(
            error="search_provider_error",
            message=f"搜索服务返回错误（HTTP {response.status_code}）。",
        )


class NullSearchProvider:
    """兜底 Provider：没有配置搜索服务时使用。

    它不报错，只是老老实实告诉 Agent"我搜不了"，
    这样 Agent 会基于已有知识写作，并在 risk_notes 里说明资料受限。
    """

    name = "none"

    def __init__(self, message: str = "") -> None:
        # 允许传入更具体的说明（比如"google 缺 SEARCH_ENGINE_ID"），
        # 这样配错时模型和用户看到的都是确切原因，而不是一句笼统的"没配置"。
        self.message = message

    def search(self, query: str, max_results: int) -> SearchResponse:
        return SearchResponse(
            error="search_provider_not_configured",
            message=(
                self.message
                or "系统没有配置联网搜索服务（SEARCH_API_KEY 为空），"
                "无法获取实时资料，请基于已有知识写作，并在 risk_notes 里明确说明资料受限。"
            ),
        )


def _base_url_for(provider_name: str) -> str:
    """挑出这个 provider 该用的 base_url。

    SEARCH_BASE_URL 是**所有 provider 共用**的一个配置项，所以切换 provider 时
    很容易忘了清空它 —— 比如从 tavily 换成 google 却没删掉那行，
    结果 Google 的请求被打到 Tavily 的地址上，报一个莫名其妙的错。

    这里做个防呆：如果配的地址一看就是**别家**的，忽略它、用默认地址，
    并打一条日志说清楚。真要自建代理或走特殊网关时，
    地址域名对得上就不会被拦。
    """
    configured = (settings.SEARCH_BASE_URL or "").strip()
    if not configured:
        return ""

    host = urlparse(configured).netloc.lower()

    # 各家默认域名的特征片段
    looks_like = {
        "google": "googleapis.com",
        "tavily": "tavily.com",
    }
    expected = looks_like.get(provider_name)

    if expected and expected not in host:
        logger.warning(
            "SEARCH_BASE_URL=%s 看起来不是 %s 的地址，本次忽略它、改用默认地址。"
            "如果你确实要自定义地址，请确认域名写对了。",
            configured,
            provider_name,
        )
        return ""

    return configured


def get_search_provider() -> SearchProvider:
    """根据 .env 配置返回可用的搜索 Provider。

    以后要加 Exa / Bing，在这里加一个 elif 分支即可 —— 工具本身不用改。
    """
    provider_name = (settings.SEARCH_PROVIDER or "").strip().lower()
    api_key = (settings.SEARCH_API_KEY or "").strip()

    if not provider_name or not api_key:
        logger.info("未配置 SEARCH_PROVIDER / SEARCH_API_KEY，使用 NullSearchProvider")
        return NullSearchProvider()

    base_url = _base_url_for(provider_name)

    if provider_name == "google":
        engine_id = (settings.SEARCH_ENGINE_ID or "").strip()
        if not engine_id:
            # 这里不去猜、也不偷偷换成别家 —— 直接说清楚缺什么，
            # 免得"配了 google 却在用 tavily"这种事让人摸不着头脑。
            logger.error("SEARCH_PROVIDER=google 但没填 SEARCH_ENGINE_ID")
            return NullSearchProvider(
                message=(
                    "搜索服务配置不完整：SEARCH_PROVIDER 填的是 google，"
                    "但 SEARCH_ENGINE_ID（搜索引擎 ID）是空的。"
                    "请去 programmablesearchengine.google.com 建一个，"
                    "并在「搜索范围」里选择「搜索整个网络」。"
                )
            )
        return GoogleSearchProvider(
            api_key=api_key,
            engine_id=engine_id,
            base_url=base_url,
            timeout=settings.SEARCH_TIMEOUT,
            language=settings.SEARCH_LANGUAGE,
        )

    if provider_name == "tavily":
        return TavilySearchProvider(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.SEARCH_TIMEOUT,
        )

    logger.warning("未知的 SEARCH_PROVIDER=%s，回退到 NullSearchProvider", provider_name)
    return NullSearchProvider()


# ---------------------------------------------------------------------------
# LangChain 工具
# ---------------------------------------------------------------------------
@tool
def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """联网搜索网页，用来获取最新、可验证的信息。

    什么时候必须用：问题涉及最新动态、新闻、数据、政策、产品更新、市场趋势、
    排名、价格、人物现状等可能随时间变化的信息时，必须先搜索，不要凭记忆回答。

    什么时候可以不用：纯创意写作、常识解释、不需要实时资料的通用问题。

    ★ 关键词语言：优先用**英文**提问。
      英文的一手资料（官方博客、研究报告、外媒报道、GitHub、论文）质量明显更好，
      而且同一件事通常英文报道更早、更完整。
      只有在需要中国本土信息时才用中文提问 —— 比如国内政策法规、中国市场数据、
      国内公司动态、中文互联网特有的产品与案例。
      一篇典型的文章：先发 2~3 个英文查询，再按需补 1 个中文查询。

    注意：搜索结果只是"线索"。要引用其中的具体内容时，应该再用 read_webpage
    打开原页面确认，不要只依赖搜索摘要。

    Args:
        query: 搜索关键词。用具体、有信息量的词，例如
            "2026 AI agent enterprise adoption report"，而不是"AI"。
        max_results: 返回几条结果，默认 5，最多 10。

    Returns:
        成功时：{"success": true, "query": "...", "count": N, "results": [...]}
        失败时：{"success": false, "error": "...", "message": "中文说明"}
    """
    # 参数兜底：模型可能传字符串或超范围数字
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "empty_query", "message": "搜索关键词不能为空。"}

    try:
        requested = int(max_results)
    except (TypeError, ValueError):
        requested = settings.AGENT_LIMITS["MAX_SEARCH_RESULTS"]

    # 双重夹紧：既不超过配置上限，也不超过硬上限
    limit = max(1, min(requested, settings.AGENT_LIMITS["MAX_SEARCH_RESULTS"], HARD_MAX_RESULTS))

    logger.info("web_search started query=%r limit=%s", query, limit)
    provider = get_search_provider()
    response = provider.search(query, limit)

    if response.error:
        logger.warning("web_search failed provider=%s error=%s", provider.name, response.error)
        return {
            "success": False,
            "error": response.error,
            "message": response.message or "搜索失败。",
            "query": query,
        }

    # ★ 把搜到的每一条都登记进来源登记簿（后面要靠它对账）
    for item in response.results:
        register_source(
            url=item.url,
            title=item.title,
            snippet=truncate(item.snippet, settings.AGENT_LIMITS["SOURCE_SNIPPET_CHARS"], ""),
            source_type="search",
            published_at=item.published_at,
        )

    logger.info("web_search completed results=%s", len(response.results))
    return {
        "success": True,
        "query": query,
        "count": len(response.results),
        "results": [item.to_dict() for item in response.results],
        # 给模型的一句提示，引导它下一步去读原文
        "next_step_hint": (
            "如果你要引用其中某条的具体内容或数据，请调用 read_webpage 打开该链接确认原文，"
            "不要仅凭搜索摘要下结论。"
        ),
    }


# Google 的 pagemap.metatags 里可能带发布日期，但键名五花八门 ——
# 各家网站用的 meta 标签不一样。按常见程度排个序，逐个碰。
_DATE_META_KEYS = (
    "article:published_time",
    "datePublished",
    "datepublished",
    "og:published_time",
    "publishdate",
    "pubdate",
    "date",
    "article:modified_time",
    "og:updated_time",
)


def _parse_pagemap_date(pagemap: Any) -> datetime | None:
    """从 Google 的 pagemap 里尽力捞一个发布日期出来。

    捞不到就返回 None —— 发布日期不是关键信息，不值得为它报错或重试。
    """
    if not isinstance(pagemap, dict):
        return None

    for metatag in pagemap.get("metatags") or []:
        if not isinstance(metatag, dict):
            continue
        for key in _DATE_META_KEYS:
            parsed = _parse_date(metatag.get(key))
            if parsed:
                return parsed
    return None


def _parse_date(value: Any) -> datetime | None:
    """把搜索服务返回的日期字符串解析成 datetime，解析不了就返回 None。

    不同搜索服务给的格式五花八门，这里只处理最常见的几种，失败就算了 ——
    发布日期不是关键信息，不值得为此报错。
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None
