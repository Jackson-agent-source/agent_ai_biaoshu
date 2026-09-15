"""
SourceRegistry —— 一次 AgentRun 中"真正出现过"的 URL 清单。

这是整个项目防 AI 编造来源的关键设计。

它解决的问题：
    大模型很容易在 sources 里写一个看起来很像真的、但根本不存在（或者
    我们从来没访问过）的 URL，比如 https://fake-example-123456.com/article。
    如果我们直接存进数据库，用户就会以为文章是有依据的。

我们的做法：
    1. 工具（web_search / read_webpage）每拿到一个 URL，就登记进 Registry。
    2. Agent 跑完以后，拿 Registry 去"对账"：
       draft.sources 里凡是 Registry 里没有的 URL，一律丢弃，并记一条风险提示。
    3. 最终入库的 Article.sources 只包含真实出现过的链接。

Registry 怎么传给工具？
    用 ContextVar。AgentService 在调用 Agent 之前调用 activate(registry)，
    工具内部调用 get_active_registry() 就能拿到"当前这一次运行"的登记簿。
    这样做的好处是：工具函数的签名保持干净（模型看到的参数只有 query / url），
    而且工具单独测试时不需要 Registry 也能跑。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from apps.common.exceptions import SourceValidationError

logger = logging.getLogger(__name__)


@dataclass
class RegisteredSource:
    """登记簿里的一条来源记录（还没写进数据库）。"""

    url: str
    title: str = ""
    snippet: str = ""
    domain: str = ""
    source_type: str = "search"        # search | webpage
    published_at: datetime | None = None
    # 是否真的被 read_webpage 读取过正文（用于区分"只是搜到"和"真的读了"）
    was_read: bool = False
    # 首次出现的顺序，方便调试
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = _domain_of(self.url)


@dataclass
class SourceRegistry:
    """一次 AgentRun 的来源登记簿。"""

    # url -> RegisteredSource
    _sources: dict[str, RegisteredSource] = field(default_factory=dict)
    _counter: int = 0

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def register(
        self,
        *,
        url: str,
        title: str = "",
        snippet: str = "",
        source_type: str = "search",
        published_at: datetime | None = None,
        was_read: bool = False,
    ) -> RegisteredSource | None:
        """登记一个来源。同一个 URL 只保留一条（自动去重）。

        返回登记后的对象；URL 非法时返回 None（不抛异常，避免打断工具执行）。
        """
        url = normalize_url(url)
        if not url:
            return None

        existing = self._sources.get(url)
        if existing is not None:
            # 已经登记过：补充信息，但不覆盖已有的非空字段
            if title and not existing.title:
                existing.title = title
            if snippet and not existing.snippet:
                existing.snippet = snippet
            if published_at and not existing.published_at:
                existing.published_at = published_at
            # 如果这次是"真的读了正文"，把类型升级为 webpage
            if was_read:
                existing.was_read = True
                existing.source_type = "webpage"
            return existing

        self._counter += 1
        source = RegisteredSource(
            url=url,
            title=title,
            snippet=snippet,
            source_type="webpage" if was_read else source_type,
            published_at=published_at,
            was_read=was_read,
            sequence=self._counter,
        )
        self._sources[url] = source
        return source

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def contains(self, url: str) -> bool:
        """这个 URL 是否在本次运行中真实出现过？"""
        return normalize_url(url) in self._sources

    def get(self, url: str) -> RegisteredSource | None:
        return self._sources.get(normalize_url(url))

    @property
    def urls(self) -> set[str]:
        """所有登记过的 URL（已规范化）。"""
        return set(self._sources.keys())

    def all(self) -> list[RegisteredSource]:
        """按首次出现顺序返回所有来源。"""
        return sorted(self._sources.values(), key=lambda s: s.sequence)

    def __len__(self) -> int:
        return len(self._sources)

    # ------------------------------------------------------------------
    # 对账：校验模型返回的 sources
    # ------------------------------------------------------------------
    def validate_draft_sources(self, draft_sources: list) -> tuple[list[dict], list[str]]:
        """用登记簿校验模型返回的 sources 列表。

        参数 draft_sources: ArticleDraft.sources，元素需要有 url / title / usage 属性
                           （Pydantic 的 SourceReference，或者等价的字典/对象）

        返回:
            (validated, risk_notes)
            validated   —— 只包含真实出现过的来源，每个是 dict，附带 registry 里的元信息
            risk_notes  —— 需要追加到文章里的风险提示（中文）

        这一层的作用：把模型"编造引用"的可能性直接掐掉。
        """
        validated: list[dict] = []
        forged: list[str] = []
        seen: set[str] = set()

        for item in draft_sources or []:
            url = normalize_url(_read_attr(item, "url"))
            if not url:
                continue

            # 同一个 URL 在 sources 里出现两次，只保留一次
            if url in seen:
                continue

            registered = self._sources.get(url)
            if registered is None:
                # ★ 关键分支：模型返回了一个我们从没检索/访问过的 URL
                forged.append(_read_attr(item, "url") or url)
                logger.warning("丢弃未经检索验证的来源: %s", url)
                continue

            seen.add(url)
            validated.append(
                {
                    "url": url,
                    "title": _read_attr(item, "title") or registered.title or url,
                    "usage": _read_attr(item, "usage") or "",
                    "snippet": registered.snippet,
                    "domain": registered.domain,
                    "published_at": registered.published_at,
                    "source_type": registered.source_type,
                    "was_read": registered.was_read,
                }
            )

        risk_notes: list[str] = []
        if forged:
            risk_notes.append(
                f"模型返回了 {len(forged)} 个未经检索验证的来源，这些来源已被系统移除，"
                "相关引用请谨慎对待。"
            )

        return validated, risk_notes

    def assert_valid(self, url: str) -> None:
        """严格模式：URL 不在登记簿里就抛异常。供测试和内部断言使用。"""
        if not self.contains(url):
            raise SourceValidationError(f"来源未在本次运行中出现过：{url}")


# ---------------------------------------------------------------------------
# ContextVar：把"当前登记簿"传给工具
# ---------------------------------------------------------------------------
_active_registry: ContextVar[SourceRegistry | None] = ContextVar("active_source_registry", default=None)


def activate(registry: SourceRegistry):
    """激活一个登记簿，返回 token（用于之后还原）。由 AgentService 调用。"""
    return _active_registry.set(registry)


def deactivate(token) -> None:
    """还原到激活之前的状态。"""
    _active_registry.reset(token)


def get_active_registry() -> SourceRegistry | None:
    """获取当前登记簿；不在 Agent 运行上下文中时返回 None。"""
    return _active_registry.get()


def register_source(**kwargs) -> RegisteredSource | None:
    """便捷函数：登记到"当前"登记簿。工具内部调用这个。"""
    registry = get_active_registry()
    if registry is None:
        return None
    return registry.register(**kwargs)


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------
def normalize_url(url: str | None) -> str:
    """规范化 URL，用于去重和比对。

    做三件事：
    1. 去掉首尾空白；
    2. 去掉末尾的 /（https://a.com/ 和 https://a.com 算同一个）；
    3. 去掉 fragment（#section，同一页面的不同锚点算同一个）。
    4. 统一小写 host，去掉默认端口。

    注意：会去掉 URL 里的 query 参数以外的追踪参数吗？不会 —— 那可能改变页面内容，
    我们只做保守的规范化。
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    if not parsed.scheme or not parsed.netloc:
        return ""

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # 去掉默认端口
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]

    path = parsed.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")

    return f"{scheme}://{netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")


def _domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
    except Exception:  # pragma: no cover
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _read_attr(item, name: str) -> str:
    """兼容 Pydantic 对象和普通 dict 两种取值方式。"""
    if isinstance(item, dict):
        value = item.get(name, "")
    else:
        value = getattr(item, name, "")
    return str(value) if value else ""
