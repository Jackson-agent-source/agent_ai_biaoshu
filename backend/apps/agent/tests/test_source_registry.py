"""
SourceRegistry 的测试 —— 防止 AI 编造来源的核心机制。
"""

from __future__ import annotations

import pytest

from apps.agent.schemas.article import SourceReference
from apps.agent.services.source_registry import (
    SourceRegistry,
    activate,
    deactivate,
    get_active_registry,
    normalize_url,
    register_source,
)
from apps.common.exceptions import SourceValidationError


class TestRegistration:
    """登记与去重。"""

    def test_register_and_query(self):
        registry = SourceRegistry()
        registry.register(url="https://example.com/a", title="标题 A", snippet="摘要")

        assert registry.contains("https://example.com/a")
        assert len(registry) == 1
        assert registry.get("https://example.com/a").title == "标题 A"

    def test_duplicate_url_only_kept_once(self):
        """同一个 URL 登记多次只算一条 —— 模型经常重复搜索。"""
        registry = SourceRegistry()
        registry.register(url="https://example.com/a", title="第一次")
        registry.register(url="https://example.com/a", title="第二次")
        registry.register(url="https://example.com/a")

        assert len(registry) == 1
        # 不覆盖已有的非空标题
        assert registry.get("https://example.com/a").title == "第一次"

    def test_url_normalization_dedupes_variants(self):
        """末尾斜杠、锚点、大小写差异应该算同一个 URL。"""
        registry = SourceRegistry()
        registry.register(url="https://Example.com/page/")
        registry.register(url="https://example.com/page")
        registry.register(url="https://example.com/page#section-2")

        assert len(registry) == 1

    def test_was_read_upgrades_source_type(self):
        """先搜到、后读过：类型应该升级成 webpage。"""
        registry = SourceRegistry()
        registry.register(url="https://example.com/a", source_type="search")
        registry.register(url="https://example.com/a", was_read=True)

        assert registry.get("https://example.com/a").source_type == "webpage"
        assert registry.get("https://example.com/a").was_read is True

    def test_invalid_url_ignored(self):
        registry = SourceRegistry()
        assert registry.register(url="") is None
        assert registry.register(url="not-a-url") is None
        assert len(registry) == 0

    def test_domain_extracted_automatically(self):
        registry = SourceRegistry()
        registry.register(url="https://www.example.com/a/b")
        assert registry.get("https://www.example.com/a/b").domain == "example.com"


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://Example.com/Path/", "https://example.com/Path"),
            ("https://example.com:443/a", "https://example.com/a"),
            ("http://example.com:80/a", "http://example.com/a"),
            ("https://example.com/a?x=1", "https://example.com/a?x=1"),
            ("https://example.com/a#frag", "https://example.com/a"),
            ("", ""),
            ("no-scheme.com/a", ""),
        ],
    )
    def test_normalization(self, raw, expected):
        assert normalize_url(raw) == expected


class TestDraftSourceValidation:
    """★ 核心：校验模型返回的 sources，把编造的链接剔除。"""

    def _registry(self) -> SourceRegistry:
        registry = SourceRegistry()
        registry.register(
            url="https://real-source.com/report",
            title="真实报告",
            snippet="这是真实检索到的来源",
        )
        registry.register(url="https://another-real.com/news", title="真实新闻")
        return registry

    def test_keeps_only_real_sources(self):
        """模型返回 1 个真来源 + 1 个假来源：只保留真的。"""
        registry = self._registry()

        draft_sources = [
            SourceReference(
                title="真实报告",
                url="https://real-source.com/report",
                usage="提供了市场规模数据",
            ),
            SourceReference(
                title="看起来很真的假来源",
                url="https://fake-example-123456.com/article",
                usage="提供了某个惊人的数字",
            ),
        ]

        validated, risk_notes = registry.validate_draft_sources(draft_sources)

        assert len(validated) == 1
        assert validated[0]["url"] == "https://real-source.com/report"
        assert validated[0]["usage"] == "提供了市场规模数据"
        # 必须留下风险提示，让用户知道有来源被删了
        assert len(risk_notes) == 1
        assert "未经检索验证" in risk_notes[0]

    def test_all_forged_sources_removed(self):
        """全部是编造的：一条都不留，并且给出提示。"""
        registry = self._registry()

        draft_sources = [
            SourceReference(title="假 1", url="https://fake-1.com/a", usage="x"),
            SourceReference(title="假 2", url="https://fake-2.com/b", usage="y"),
        ]

        validated, risk_notes = registry.validate_draft_sources(draft_sources)

        assert validated == []
        assert len(risk_notes) == 1
        assert "2 个" in risk_notes[0]

    def test_registered_but_not_cited_is_not_returned(self):
        """登记簿里有，但模型没引用：不进最终来源列表。"""
        registry = self._registry()
        validated, risk_notes = registry.validate_draft_sources([])

        assert validated == []
        assert risk_notes == []
        # 但登记簿本身还是 2 条 —— 它记录的是"检索过什么"
        assert len(registry) == 2

    def test_duplicate_citations_collapsed(self):
        """模型把同一个来源写了两遍：只保留一条。"""
        registry = self._registry()
        source = SourceReference(title="真实报告", url="https://real-source.com/report", usage="x")

        validated, _ = registry.validate_draft_sources([source, source])

        assert len(validated) == 1

    def test_url_variant_still_matches(self):
        """模型返回的 URL 少了末尾斜杠，也应该匹配上。"""
        registry = self._registry()
        validated, risk_notes = registry.validate_draft_sources(
            [SourceReference(title="t", url="https://another-real.com/news/", usage="u")]
        )

        assert len(validated) == 1
        assert risk_notes == []

    def test_empty_and_none_input(self):
        registry = self._registry()
        assert registry.validate_draft_sources([]) == ([], [])
        assert registry.validate_draft_sources(None) == ([], [])

    def test_assert_valid_raises_for_unknown(self):
        registry = self._registry()
        registry.assert_valid("https://real-source.com/report")
        with pytest.raises(SourceValidationError):
            registry.assert_valid("https://fake.com/x")


class TestContextVarActivation:
    """ContextVar 传递机制 —— 工具靠它拿到"当前这次运行"的登记簿。"""

    def test_activate_and_deactivate(self):
        assert get_active_registry() is None

        registry = SourceRegistry()
        token = activate(registry)
        try:
            assert get_active_registry() is registry
            register_source(url="https://example.com/a", title="通过便捷函数登记")
            assert registry.contains("https://example.com/a")
        finally:
            deactivate(token)

        assert get_active_registry() is None

    def test_register_without_active_registry_is_noop(self):
        """没有激活登记簿时，工具不能崩 —— 单独调用工具也要能用。"""
        assert get_active_registry() is None
        assert register_source(url="https://example.com/a") is None
