"""定时命令到文章 API 的完整链路，仅替换外部 HTTP 和模型。"""
import httpx
import pytest
from django.core.management import call_command

from apps.agent.models import AgentRun, Source, ToolCall
from apps.agent.tests.fakes import make_article_draft_message, make_tool_call_message, sample_draft_args
from apps.trends.models import TopicCandidate, TrendFetchRun
from apps.trends import providers


@pytest.mark.django_db
def test_scheduled_collection_to_article_api(user, auth_client, settings, mocker, patch_model, fake_search):
    settings.TREND_AUTO_GENERATE = True
    settings.TREND_USERNAMES = [user.username]
    settings.TREND_DAILY_ARTICLE_LIMIT = 1
    mocker.patch("apps.trends.services.model_factory.is_model_configured", return_value=False)
    # 真实 RSS 解析与采集入库；公开榜单失败仍可依靠账号关注方向完成流程。
    mocker.patch.dict(providers.PROVIDERS, {"bbc_tech": providers.fetch_bbc_technology}, clear=True)
    real_client = httpx.Client
    xml = b'<rss><channel><item><title>OpenAI new tools</title><link>https://example.org/ai</link><guid>ai-1</guid></item></channel></rss>'
    mocker.patch.object(providers.httpx, "Client", side_effect=lambda **kw: real_client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=xml)), **kw,
    ))
    patch_model([
        make_tool_call_message("web_search", {"query": "AI tools"}, "search-1"),
        make_article_draft_message(sample_draft_args(sources=[{
            "title": "报告", "url": "https://real-report.com/2026-ai-agent", "usage": "背景",
        }])),
    ])

    call_command("run_trend_pipeline")
    candidate = TopicCandidate.objects.get(status="generated")
    assert candidate.article.status == "draft"
    assert candidate.article.user_id == user.pk
    assert TrendFetchRun.objects.filter(status="success").count() == 2
    assert AgentRun.objects.get().status == "completed"
    assert Source.objects.filter(is_cited=True).count() == 1
    assert ToolCall.objects.count() == 1
    response = auth_client.get(f"/api/v1/articles/{candidate.article_id}")
    assert response.status_code == 200
    assert response.json()["data"]["title"] == candidate.article.title
    call_command("run_trend_pipeline")
    assert TopicCandidate.objects.count() == 1
    assert AgentRun.objects.count() == 1
