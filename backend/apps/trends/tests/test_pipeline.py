from __future__ import annotations

import httpx
import json
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone as dt_timezone
from django.core.management import call_command
from django.test import override_settings

from apps.articles.models import Article
from apps.agent.agents.content_agent import _recover_trend_draft_as_json
from apps.agent.services.source_registry import SourceRegistry, activate, deactivate
from apps.trends import providers
from apps.trends.categories import classify_title
from apps.trends.models import TopicCandidate, TrendItem, TrendPreference
from apps.trends.providers import TrendRecord
from apps.trends.services import TopicBatch, TopicIdea, auto_generate, build_topics, collect_focus_trends, collect_trends, generate_candidate


@pytest.mark.django_db
def test_auto_generation_retries_failed_candidate(user, mocker, settings):
    settings.TREND_AUTO_GENERATE = True
    settings.TREND_DAILY_ARTICLE_LIMIT = 1
    candidate = TopicCandidate.objects.create(user=user, cluster_key="retry", topic="重试", category="focus", status="failed")
    generate = mocker.patch("apps.trends.services.generate_candidate")
    auto_generate(user=user)
    assert generate.call_args.kwargs["candidate"].pk == candidate.pk


@pytest.mark.django_db
def test_daily_limit_uses_shanghai_day_without_database_date_conversion(user, mocker, settings):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    settings.TREND_AUTO_GENERATE = True
    settings.TREND_DAILY_ARTICLE_LIMIT = 1
    # UTC 前一天 16:30 已是上海当天；前一天 15:30 不计入当天。
    mocker.patch("apps.trends.services.timezone.now", return_value=datetime(2026, 10, 18, 1, tzinfo=dt_timezone.utc))
    article = Article.objects.create(user=user, title="今日草稿")
    Article.objects.filter(pk=article.pk).update(created_at=datetime(2026, 10, 17, 16, 30, tzinfo=dt_timezone.utc))
    TopicCandidate.objects.create(user=user, cluster_key="done", topic="已完成", category="focus", status="generated", article=article)
    TopicCandidate.objects.create(user=user, cluster_key="ready", topic="候选", category="focus")
    generate = mocker.patch("apps.trends.services.generate_candidate")
    with CaptureQueriesContext(connection) as queries:
        auto_generate(user=user)
    generate.assert_not_called()
    assert not any("django_datetime_cast_date" in q["sql"] for q in queries)
    Article.objects.filter(pk=article.pk).update(created_at=datetime(2026, 10, 17, 15, 30, tzinfo=dt_timezone.utc))
    auto_generate(user=user)
    generate.assert_called_once()


@pytest.mark.django_db
def test_collection_is_idempotent_and_other_platforms_continue(user, mocker):
    mocker.patch.dict(providers.PROVIDERS, {
        "google": lambda: [TrendRecord("google", "g1", "同一个热点", "https://example.org/g", "US", 1)],
        "youtube": lambda: [TrendRecord("youtube", "v1", "同一个热点", "https://youtube.com/watch?v=v1", "US", 2)],
    })
    mocker.patch("apps.trends.services.PROVIDERS", providers.PROVIDERS)
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=False)

    first = collect_trends(["google", "youtube"])
    second = collect_trends(["google", "youtube"])
    assert all(run.status == "success" for run in first + second)
    assert TrendItem.objects.count() == 2

    candidates = build_topics(user=user)
    assert len(candidates) == 1
    assert candidates[0].trends.count() == 2
    assert candidates[0].score > 60
    assert build_topics(user=user)[0].pk == candidates[0].pk


@pytest.mark.django_db
def test_generate_candidate_links_draft_once(user, mocker):
    trend = TrendItem.objects.create(platform="google", external_id="g2", title="新热点", url="https://example.org/t", rank=1)
    candidate = TopicCandidate.objects.create(user=user, cluster_key="today:topic", topic="这个热点发生了什么？")
    candidate.trends.add(trend)
    article = Article.objects.create(user=user, title="测试草稿")
    generate = mocker.patch("apps.trends.services.AgentService.generate_article", return_value=(article, object()))

    result = generate_candidate(candidate=candidate, user=user)
    candidate.refresh_from_db()
    assert result.pk == article.pk
    assert candidate.status == "generated"
    assert candidate.article_id == article.pk
    assert generate.call_args.kwargs["request_data"]["research_mode"] is True
    with pytest.raises(ValueError):
        generate_candidate(candidate=candidate, user=user)
    assert generate.call_count == 1


@pytest.mark.django_db
def test_topic_api_enforces_owner(auth_client, other_auth_client, user, mocker):
    candidate = TopicCandidate.objects.create(user=user, cluster_key="owner:topic", topic="测试")
    response = other_auth_client.post(f"/api/v1/trends/topics/{candidate.pk}/generate", {}, format="json")
    assert response.status_code == 404
    assert TopicCandidate.objects.get(pk=candidate.pk).status == "ready"


@pytest.mark.django_db
def test_overview_returns_traceable_sources(auth_client, user):
    trend = TrendItem.objects.create(platform="google", external_id="g4", title="可追溯热点", url="https://example.org/source", rank=1)
    candidate = TopicCandidate.objects.create(user=user, cluster_key="overview:topic", topic="这条热点的背景是什么？")
    candidate.trends.add(trend)
    response = auth_client.get("/api/v1/trends/overview")
    assert response.status_code == 200
    item = response.json()["data"]["candidates"][0]
    assert item["topic"] == "这条热点的背景是什么？"
    assert item["trends"][0]["url"] == "https://example.org/source"
    assert "hacker_news" in {source["key"] for source in response.json()["data"]["source_catalog"]}


def test_google_rss_parser(mocker):
    xml = b"""<?xml version='1.0'?><rss><channel><item><title>Example trend</title>
    <link>https://example.org/story</link><pubDate>Sun, 27 Sep 2026 08:00:00 GMT</pubDate>
    </item></channel></rss>"""
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=xml))
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs))
    with override_settings(TREND_GOOGLE_GEO="US", TREND_FETCH_TIMEOUT=5):
        result = providers.fetch_google()
    assert len(result) == 1
    assert result[0].title == "Example trend"
    assert result[0].url == "https://example.org/story"


def test_bbc_rss_keeps_source_link_and_date(mocker):
    xml = b"""<rss><channel><item><title>New science story</title>
    <link>https://www.bbc.com/news/science-123</link><guid>bbc-123</guid>
    <pubDate>Sun, 27 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>"""
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=xml))
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs))
    result = providers.fetch_bbc_technology()
    assert result[0].platform == "bbc_tech"
    assert result[0].url == "https://www.bbc.com/news/science-123"
    assert result[0].published_at.year == 2026


def test_rss_html_response_is_reported_as_source_failure(mocker):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>"))
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(providers.TrendProviderError, match="返回网页"):
        providers.fetch_36kr()


def test_github_search_uses_repository_link(mocker):
    real_client = httpx.Client
    def reply(request):
        assert request.url.path == "/search/repositories"
        return httpx.Response(200, json={"items": [{
            "id": 42, "full_name": "example/ai-tool", "html_url": "https://github.com/example/ai-tool",
            "stargazers_count": 12, "created_at": "2026-09-27T08:00:00Z",
        }]})
    transport = httpx.MockTransport(reply)
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs))
    with override_settings(TREND_GITHUB_TOKEN=""):
        result = providers.fetch_github()
    assert result[0].external_id == "42"
    assert result[0].metric == 12
    assert result[0].url == "https://github.com/example/ai-tool"


def test_focus_news_parser(mocker):
    xml = b"""<?xml version='1.0'?><rss><channel><item><title>OpenAI launches new model</title>
    <link>https://news.google.com/rss/articles/example</link><guid>story-1</guid>
    <pubDate>Sun, 27 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>"""
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=xml))
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs))
    result = providers.fetch_focus_news(["OpenAI", "大模型"], user_id=1)
    assert len(result) == 1
    assert result[0].platform == "focus_news"
    assert result[0].title == "OpenAI launches new model"


def test_general_trends_are_categorized():
    assert classify_title("Portugal vs Norway football") == "sports"
    assert classify_title("OpenAI 发布新模型") == "technology"
    assert classify_title("股市利率变化") == "finance"
    assert classify_title("未知新词") == "other"


@pytest.mark.django_db
def test_general_ai_trend_joins_user_focus(user, mocker):
    TrendItem.objects.create(
        platform="google", external_id="ai-general", title="OpenAI releases model",
        category="technology", url="https://example.org/ai", rank=1,
    )
    TrendItem.objects.create(
        platform="google", external_id="sports-general", title="Portugal vs Norway football",
        category="sports", url="https://example.org/sports", rank=2,
    )
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=False)
    candidates = build_topics(user=user)
    assert {candidate.category for candidate in candidates} == {"focus", "sports"}


@pytest.mark.django_db
def test_focus_trends_and_preferences_are_user_scoped(auth_client, user, other_user, mocker):
    mocker.patch("apps.trends.services.fetch_focus_news", return_value=[
        TrendRecord("focus_news", "u1-story", "OpenAI 发布新模型", "https://news.google.com/rss/articles/ai", "CN", 1),
    ])
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=False)
    collect_focus_trends(user=user)
    assert build_topics(user=user)[0].category == "focus"
    assert build_topics(user=other_user) == []

    response = auth_client.get("/api/v1/trends/overview")
    assert response.status_code == 200
    assert response.json()["data"]["categories"][0]["key"] == "focus"
    assert response.json()["data"]["preferences"]["auto_categories"] == ["focus"]

    changed = auth_client.put("/api/v1/trends/preferences", {
        "focus_terms": ["机器人", "大模型"], "auto_categories": ["technology"],
    }, format="json")
    assert changed.status_code == 200
    assert TrendPreference.objects.get(user=user).focus_terms == ["机器人", "大模型"]
    assert TopicCandidate.objects.get(user=user).status == TopicCandidate.Status.SKIPPED
    assert TrendItem.objects.get(owner=user).category == "focus"


@pytest.mark.django_db
def test_ai_topic_and_daily_auto_limit(user, mocker, settings):
    TrendItem.objects.create(platform="google", external_id="g3", title="人工智能新趋势", category="focus", url="https://example.org/ai", rank=1)
    settings.TREND_KEYWORDS = ["人工智能"]
    settings.TREND_AUTO_GENERATE = True
    settings.TREND_DAILY_ARTICLE_LIMIT = 1
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=True)
    model = mocker.Mock()
    model.with_structured_output.return_value.invoke.return_value = TopicBatch(ideas=[
        TopicIdea(index=0, topic="人工智能新趋势会怎样影响日常工作？", angle="工作流程", reason="受关注")
    ])
    mocker.patch("apps.trends.services.model_factory.get_chat_model", return_value=model)
    candidate = build_topics(user=user)[0]
    assert candidate.topic == "人工智能新趋势会怎样影响日常工作？"
    assert build_topics(user=user)[0].pk == candidate.pk
    assert model.with_structured_output.return_value.invoke.call_count == 1

    fake_generate = mocker.patch("apps.trends.services.generate_candidate")
    auto_generate(user=user)
    assert fake_generate.call_count == 1

    article = Article.objects.create(user=user, title="已有热点文章")
    candidate.status = TopicCandidate.Status.GENERATED
    candidate.article = article
    candidate.save()
    auto_generate(user=user)
    assert fake_generate.call_count == 1


@pytest.mark.django_db
def test_scheduled_pipeline_uses_only_active_user(user, other_user, mocker, settings):
    settings.TREND_USER_ID = 0
    mocker.patch("apps.trends.management.commands.run_trend_pipeline.collect_trends", return_value=[])
    mocker.patch("apps.trends.management.commands.run_trend_pipeline.collect_focus_trends", return_value=SimpleNamespace(status="success", item_count=0, message=""))
    build = mocker.patch("apps.trends.management.commands.run_trend_pipeline.build_topics", return_value=[])
    generate = mocker.patch("apps.trends.management.commands.run_trend_pipeline.auto_generate", return_value=[])
    other_user.is_active = False
    other_user.save(update_fields=["is_active"])

    call_command("run_trend_pipeline")
    assert build.call_args.kwargs["user"].pk == user.pk
    assert generate.call_args.kwargs["user"].pk == user.pk


@pytest.mark.django_db
def test_scheduled_pipeline_builds_and_generates_for_both_named_users(user, other_user, mocker, settings):
    settings.TREND_USERNAMES = [user.username, other_user.username]
    collect = mocker.patch("apps.trends.management.commands.run_trend_pipeline.collect_trends", return_value=[])
    focus = mocker.patch("apps.trends.management.commands.run_trend_pipeline.collect_focus_trends", return_value=SimpleNamespace(status="success", item_count=0, message=""))
    build = mocker.patch("apps.trends.management.commands.run_trend_pipeline.build_topics", return_value=[])
    generate = mocker.patch("apps.trends.management.commands.run_trend_pipeline.auto_generate", return_value=[])

    call_command("run_trend_pipeline")

    collect.assert_called_once_with()
    assert [call.kwargs["user"].pk for call in focus.call_args_list] == [user.pk, other_user.pk]
    assert [call.kwargs["user"].pk for call in build.call_args_list] == [user.pk, other_user.pk]
    assert [call.kwargs["user"].pk for call in generate.call_args_list] == [user.pk, other_user.pk]


@pytest.mark.django_db
def test_deepseek_topic_uses_supported_json_mode(user, mocker, settings):
    settings.LLM_PROVIDER = "deepseek"
    TrendItem.objects.create(platform="google", external_id="g5", title="机器人新工具", url="https://example.org/robot", rank=1)
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=True)
    model = mocker.Mock()
    model.bind.return_value.invoke.return_value = SimpleNamespace(content='{"ideas":[{"index":0,"topic":"机器人新工具如何应用？","angle":"应用","reason":"趋势"}]}')
    mocker.patch("apps.trends.services.model_factory.get_chat_model", return_value=model)

    candidate = build_topics(user=user)[0]
    assert candidate.topic == "机器人新工具如何应用？"
    model.bind.assert_called_once_with(response_format={"type": "json_object"})


def test_trend_article_fallback_uses_registered_sources(mocker, settings):
    settings.LLM_PROVIDER = "deepseek"
    registry = SourceRegistry()
    registry.register(url="https://example.org/report", title="报告", snippet="已检索到的摘要")
    token = activate(registry)
    model = mocker.Mock()
    model.bind.return_value.invoke.return_value = SimpleNamespace(content=json.dumps({
        "title": "热点解读", "subtitle": None, "summary": "概要", "hook": "开头",
        "outline": ["背景", "影响"], "body_markdown": "## 背景\n\n" + "这是一段需要复核的分析。" * 20,
        "image_suggestions": [],
        "sources": [{"title": "报告", "url": "https://example.org/report", "usage": "提供背景"}],
        "risk_notes": [],
    }, ensure_ascii=False))
    mocker.patch("apps.agent.agents.content_agent.get_chat_model", return_value=model)
    try:
        draft = _recover_trend_draft_as_json(user_prompt="请写热点解读")
    finally:
        deactivate(token)
    assert draft is not None
    assert draft.sources[0].url == "https://example.org/report"
    assert any("网页正文" in note for note in draft.risk_notes)
