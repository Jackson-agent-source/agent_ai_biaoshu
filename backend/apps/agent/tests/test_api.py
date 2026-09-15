"""
接口层测试。

覆盖需求文档明确列出的用例：
    - 创建文章接口
    - 缺 topic
    - topic 太长
    - Article Detail
    - Article Update
    - 用户不能访问别人的 Article

重点在**边界和越权**，而不是把 happy path 重复测一遍。
"""

from __future__ import annotations

import pytest
from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from apps.agent.models import Source
from apps.agent.serializers import TOPIC_MAX_LENGTH
from apps.agent.tests.conftest import request_data
from apps.agent.tests.fakes import make_article_draft_message, make_tool_call_message, sample_draft_args
from apps.articles.models import Article

User = get_user_model()

RUNS_URL = "/api/v1/agent/runs"
ARTICLES_URL = "/api/v1/articles"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
@pytest.fixture
def article(user):
    """一篇属于 user 的文章，用来测详情 / 编辑 / 越权。"""
    return Article.objects.create(
        user=user,
        title="测试文章标题",
        subtitle="副标题",
        summary="这是一段摘要。",
        hook="开头钩子。",
        outline=["第一点", "第二点"],
        content_markdown="## 第一节\n\n正文内容。",
        content_html="<h2>第一节</h2><p>正文内容。</p>",
        image_suggestions=["配图建议"],
        source_ids=[],
        risk_notes=["示例风险提示"],
        status=Article.Status.DRAFT,
        version=1,
    )


# ===========================================================================
# 创建文章接口
# ===========================================================================
@pytest.mark.django_db
class TestCreateArticleAPI:
    """POST /api/v1/agent/runs"""

    def test_create_article_success(self, auth_client, fake_search, fake_webpage, patch_model):
        """走完整链路，返回 201 和统一信封。"""
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent", "max_results": 5}, "c1"),
                make_article_draft_message(
                    sample_draft_args(
                        title="接口测试生成的文章",
                        sources=[
                            {
                                "title": "2026 年 AI Agent 行业报告",
                                "url": "https://real-report.com/2026-ai-agent",
                                "usage": "提供了行业数据",
                            }
                        ],
                    )
                ),
            ]
        )

        response = auth_client.post(RUNS_URL, request_data(), format="json")

        assert response.status_code == 201
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "completed"
        assert body["data"]["tool_call_count"] == 1
        assert body["data"]["article_id"]
        assert body["data"]["run_id"]

        # 真的落库了，而且是草稿
        article = Article.objects.get(id=body["data"]["article_id"])
        assert article.title == "接口测试生成的文章"
        assert article.status == Article.Status.DRAFT  # ★ 永远不会自动发布

    def test_response_never_exposes_secrets(
        self, auth_client, fake_search, fake_webpage, patch_model
    ):
        """★ 接口返回里不能出现任何密钥、堆栈、内部路径。"""
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        response = auth_client.post(RUNS_URL, request_data(), format="json")
        raw = response.content.decode("utf-8")

        for forbidden in ("api_key", "API_KEY", "sk-", "Traceback", "File \"", "settings.py"):
            assert forbidden not in raw, f"接口返回里出现了敏感内容：{forbidden}"

    def test_missing_topic_rejected(self, auth_client):
        """缺 topic：400 + 中文提示，而且**不会**去调用大模型。"""
        payload = request_data()
        payload.pop("topic")

        response = auth_client.post(RUNS_URL, payload, format="json")

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "主题" in str(body["error"])
        assert body["error"]["request_id"]  # 方便用户报障时定位

    def test_blank_topic_rejected(self, auth_client):
        response = auth_client.post(RUNS_URL, request_data(topic="   "), format="json")

        assert response.status_code == 400
        assert "主题" in str(response.json()["error"])

    def test_topic_too_long_rejected(self, auth_client):
        """topic 太长：400。★ 防止有人灌超长 prompt 烧钱。"""
        response = auth_client.post(
            RUNS_URL, request_data(topic="AI" * (TOPIC_MAX_LENGTH + 10)), format="json"
        )

        assert response.status_code == 400
        assert "太长" in str(response.json()["error"])

    def test_total_prompt_too_long_rejected(self, auth_client):
        """单字段都合规、加起来超长 —— 也要拦下来。

        说明：默认配置下各字段上限之和（500+100+200+200+2000=3000）
        小于 MAX_USER_PROMPT_CHARS（5000），所以这条总长度检查平时不会触发，
        它是"万一有人把单字段上限调大"时的兜底。这里把上限调小来验证它确实生效。
        """
        from django.conf import settings as dj_settings
        from django.test import override_settings

        with override_settings(
            AGENT_LIMITS={**dj_settings.AGENT_LIMITS, "MAX_USER_PROMPT_CHARS": 100}
        ):
            response = auth_client.post(
                RUNS_URL,
                request_data(extra_requirements="补充要求" * 50),
                format="json",
            )

        assert response.status_code == 400
        assert "总长度" in str(response.json()["error"])

    def test_invalid_length_choice_rejected(self, auth_client):
        response = auth_client.post(RUNS_URL, request_data(length="huge"), format="json")

        assert response.status_code == 400
        assert "short" in str(response.json()["error"])

    def test_requires_authentication(self, api_client):
        """未登录：401，不泄露任何业务信息。"""
        response = api_client.post(RUNS_URL, request_data(), format="json")

        assert response.status_code == 401
        assert response.json()["success"] is False

    def test_agent_failure_returns_safe_error(self, auth_client, mocker):
        """Agent 挂了：返回 502 + 中文提示，绝不返回堆栈。"""
        from apps.common.exceptions import AgentExecutionError

        mocker.patch(
            "apps.agent.services.agent_service.run_content_agent",
            side_effect=AgentExecutionError("模型调用失败，请稍后重试。"),
        )

        response = auth_client.post(RUNS_URL, request_data(), format="json")
        raw = response.content.decode("utf-8")

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AGENT_EXECUTION_FAILED"
        assert "Traceback" not in raw

    def test_conversation_of_other_user_rejected(
        self, auth_client, other_user, fake_search, patch_model
    ):
        """★ 越权：不能往别人的会话里写内容。"""
        from apps.conversations.models import Conversation

        other_conversation = Conversation.objects.create(user=other_user, title="别人的会话")

        response = auth_client.post(
            RUNS_URL,
            request_data(conversation_id=str(other_conversation.id)),
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


# ===========================================================================
# 运行记录
# ===========================================================================
@pytest.mark.django_db
class TestAgentRunViews:
    def test_list_only_returns_my_runs(self, auth_client, user, other_user):
        """★ 列表只能看到自己的运行记录。"""
        from apps.conversations.models import Conversation
        from apps.agent.models import AgentRun

        mine = Conversation.objects.create(user=user, title="我的")
        theirs = Conversation.objects.create(user=other_user, title="别人的")
        AgentRun.objects.create(conversation=mine, model_name="m", input_text="a")
        AgentRun.objects.create(conversation=theirs, model_name="m", input_text="b")

        response = auth_client.get(RUNS_URL)

        assert response.status_code == 200
        results = response.json()["data"]["results"]
        assert len(results) == 1
        assert response.json()["data"]["count"] == 1

    def test_cannot_read_other_users_run(self, auth_client, other_user):
        """★ 越权：别人的 run_id 直接 404。"""
        from apps.conversations.models import Conversation
        from apps.agent.models import AgentRun

        theirs = Conversation.objects.create(user=other_user, title="别人的")
        run = AgentRun.objects.create(conversation=theirs, model_name="m")

        response = auth_client.get(f"{RUNS_URL}/{run.id}")

        assert response.status_code == 404

    def test_tool_calls_endpoint(self, auth_client, user, fake_search, fake_webpage, patch_model):
        """工具调用时间线接口。"""
        from apps.agent.models import AgentRun

        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )
        auth_client.post(RUNS_URL, request_data(), format="json")

        run = AgentRun.objects.filter(conversation__user=user).latest("created_at")
        response = auth_client.get(f"{RUNS_URL}/{run.id}/tool-calls")

        assert response.status_code == 200
        calls = response.json()["data"]
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "web_search"
        assert calls[0]["status"] == "success"
        assert calls[0]["result_preview"]

    def test_sources_endpoint(self, auth_client, user, fake_search, fake_webpage, patch_model):
        from apps.agent.models import AgentRun

        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )
        auth_client.post(RUNS_URL, request_data(), format="json")

        run = AgentRun.objects.filter(conversation__user=user).latest("created_at")
        response = auth_client.get(f"{RUNS_URL}/{run.id}/sources")

        assert response.status_code == 200
        sources = response.json()["data"]
        assert len(sources) == 2
        assert all(s["url"].startswith("https://") for s in sources)


# ===========================================================================
# 文章接口
# ===========================================================================
@pytest.mark.django_db
class TestArticleAPI:
    def test_list_my_articles(self, auth_client, article):
        response = auth_client.get(ARTICLES_URL)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["count"] == 1
        assert data["results"][0]["title"] == "测试文章标题"
        # 列表页不返回正文，省流量
        assert "content_markdown" not in data["results"][0]

    def test_list_excludes_archived_by_default(self, auth_client, article):
        Article.objects.create(user=article.user, title="已归档", status=Article.Status.ARCHIVED)

        response = auth_client.get(ARTICLES_URL)

        assert response.json()["data"]["count"] == 1

    def test_list_keyword_filter(self, auth_client, article):
        assert auth_client.get(f"{ARTICLES_URL}?keyword=测试").json()["data"]["count"] == 1
        assert auth_client.get(f"{ARTICLES_URL}?keyword=不存在的词").json()["data"]["count"] == 0

    def test_article_detail(self, auth_client, article):
        """Article Detail：包含正文、风险提示、来源。"""
        response = auth_client.get(f"{ARTICLES_URL}/{article.id}")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == str(article.id)
        assert data["title"] == "测试文章标题"
        assert data["content_markdown"].startswith("## ")
        assert "<h2>" in data["content_html"]
        assert data["risk_notes"] == ["示例风险提示"]
        assert data["sources"] == []
        assert data["status"] == "draft"

    def test_article_detail_sources_come_from_db(self, auth_client, user, article):
        """文章引用的来源必须是数据库里的真实记录。"""
        from apps.agent.models import AgentRun
        from apps.conversations.models import Conversation

        conversation = Conversation.objects.create(user=user, title="会话")
        run = AgentRun.objects.create(conversation=conversation, model_name="m")
        source = Source.objects.create(
            agent_run=run,
            title="真实来源",
            url="https://real-source.com/report",
            snippet="摘要",
            is_cited=True,
            usage="提供了数据",
        )
        article.source_ids = [str(source.id)]
        article.save(update_fields=["source_ids"])

        response = auth_client.get(f"{ARTICLES_URL}/{article.id}")

        sources = response.json()["data"]["sources"]
        assert len(sources) == 1
        assert sources[0]["url"] == "https://real-source.com/report"
        assert sources[0]["usage"] == "提供了数据"

    def test_article_update(self, auth_client, article):
        """Article Update：改标题和正文，版本号 +1，HTML 重新渲染。"""
        response = auth_client.patch(
            f"{ARTICLES_URL}/{article.id}",
            {"title": "改过的标题", "content_markdown": "## 新的一节\n\n新内容。"},
            format="json",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["title"] == "改过的标题"
        assert "<h2>新的一节</h2>" in data["content_html"]
        assert data["version"] == 2

        article.refresh_from_db()
        assert article.title == "改过的标题"
        assert article.version == 2

    def test_article_update_status(self, auth_client, article):
        response = auth_client.patch(
            f"{ARTICLES_URL}/{article.id}", {"status": "approved"}, format="json"
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "approved"

    def test_article_update_rejects_unknown_field(self, auth_client, article):
        """只传非法字段 = 没有可更新的字段。"""
        response = auth_client.patch(
            f"{ARTICLES_URL}/{article.id}", {"user": 999}, format="json"
        )

        assert response.status_code == 400

    def test_article_update_cannot_change_ownership(self, auth_client, article, other_user):
        """★ 溯源信息不可篡改：user 字段即使传了也不生效。"""
        auth_client.patch(
            f"{ARTICLES_URL}/{article.id}",
            {"title": "新标题", "user": other_user.id},
            format="json",
        )

        article.refresh_from_db()
        assert article.user_id != other_user.id

    def test_article_update_rejects_bad_status(self, auth_client, article):
        response = auth_client.patch(
            f"{ARTICLES_URL}/{article.id}", {"status": "published"}, format="json"
        )

        assert response.status_code == 400
        assert "draft" in str(response.json()["error"])

    def test_article_delete_is_soft(self, auth_client, article):
        """删除是软删除：数据还在，只是变成 archived。"""
        response = auth_client.delete(f"{ARTICLES_URL}/{article.id}")

        assert response.status_code == 200
        article.refresh_from_db()
        assert article.status == Article.Status.ARCHIVED
        assert Article.objects.filter(id=article.id).exists()

    def test_article_stats(self, auth_client, article):
        response = auth_client.get(f"{ARTICLES_URL}/stats/")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["draft"] == 1
        assert "recent_articles" in data
        assert "recent_runs" in data


# ===========================================================================
# 越权访问
# ===========================================================================
@pytest.mark.django_db
class TestCrossUserIsolation:
    """★ 用户不能访问别人的 Article —— 这是需求文档明确要求的用例。"""

    def test_cannot_read_other_users_article(self, other_auth_client, article):
        response = other_auth_client.get(f"{ARTICLES_URL}/{article.id}")

        # 用 404 而不是 403：连"这个 id 存不存在"都不泄露
        assert response.status_code == 404
        assert response.json()["success"] is False

    def test_cannot_update_other_users_article(self, other_auth_client, article):
        response = other_auth_client.patch(
            f"{ARTICLES_URL}/{article.id}", {"title": "我改了别人的文章"}, format="json"
        )

        assert response.status_code == 404
        article.refresh_from_db()
        assert article.title == "测试文章标题"

    def test_cannot_delete_other_users_article(self, other_auth_client, article):
        response = other_auth_client.delete(f"{ARTICLES_URL}/{article.id}")

        assert response.status_code == 404
        article.refresh_from_db()
        assert article.status == Article.Status.DRAFT

    def test_cannot_read_other_users_article_sources(self, other_auth_client, article):
        response = other_auth_client.get(f"{ARTICLES_URL}/{article.id}/sources")

        assert response.status_code == 404

    def test_article_list_is_scoped_to_user(self, other_auth_client, article):
        response = other_auth_client.get(ARTICLES_URL)

        assert response.status_code == 200
        assert response.json()["data"]["count"] == 0

    def test_cannot_read_other_users_conversation_messages(
        self, auth_client, other_user
    ):
        from apps.conversations.models import Conversation

        theirs = Conversation.objects.create(user=other_user, title="别人的会话")

        response = auth_client.get(f"/api/v1/agent/conversations/{theirs.id}/messages")

        assert response.status_code == 404


# ===========================================================================
# 健康检查与认证
# ===========================================================================
@pytest.mark.django_db
class TestMisc:
    def test_health_check(self, api_client):
        response = api_client.get("/health/")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"

    def test_request_id_header_present(self, api_client):
        """每个响应都带 X-Request-ID，方便排查问题。"""
        response = api_client.get("/health/")

        assert response["X-Request-ID"]

    def test_login_and_me(self, api_client, user):
        response = api_client.post(
            "/api/v1/auth/login",
            {"username": "tester", "password": "test-password-123"},
            format="json",
        )

        assert response.status_code == 200
        token = response.json()["data"]["token"]
        assert token

        me = api_client.get("/api/v1/auth/me", HTTP_AUTHORIZATION=f"Token {token}")
        assert me.status_code == 200
        assert me.json()["data"]["username"] == "tester"

    def test_login_with_wrong_password(self, api_client, user):
        response = api_client.post(
            "/api/v1/auth/login",
            {"username": "tester", "password": "wrong-password"},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_register_creates_user(self, api_client):
        response = api_client.post(
            "/api/v1/auth/register",
            {"username": "newbie", "password": "a-strong-password-123"},
            format="json",
        )

        assert response.status_code in (200, 201)
        assert User.objects.filter(username="newbie").exists()

    def test_register_rejects_short_password(self, api_client):
        response = api_client.post(
            "/api/v1/auth/register",
            {"username": "newbie2", "password": "123"},
            format="json",
        )

        assert response.status_code == 400
        assert not User.objects.filter(username="newbie2").exists()

    def test_password_never_returned(self, api_client, user):
        """★ 任何接口都不能把密码（或哈希）返回给前端。"""
        response = api_client.post(
            "/api/v1/auth/login",
            {"username": "tester", "password": "test-password-123"},
            format="json",
        )
        raw = response.content.decode("utf-8")

        assert "password" not in raw
        assert "pbkdf2" not in raw
        assert "test-password-123" not in raw


@pytest.mark.django_db
class TestUrlRouting:
    """路由顺序的回归测试。"""

    def test_stats_is_not_treated_as_uuid(self, auth_client):
        """/articles/stats/ 不能被 <uuid:article_id> 抢走。"""
        response = auth_client.get(reverse("articles:stats"))

        assert response.status_code == 200
        assert response.json()["success"] is True


# ===========================================================================
# 部署到公网时的防护（注册开关 / 限流额度）
# ===========================================================================
@pytest.mark.django_db
class TestDeploymentGuards:
    """这些是"给一小群人用"时必须成立的行为。

    没有它们的话，公网上的陌生人注册之后就能用你的 LLM_API_KEY 生成文章，
    而且可以写个循环把额度刷光 —— 这两种都是真金白银的损失。
    """

    def test_register_works_when_enabled(self, api_client):
        """默认（本地开发）注册仍然是通的，行为没变。"""
        assert dj_settings.ALLOW_REGISTRATION is True

        response = api_client.post(
            "/api/v1/auth/register",
            {"username": "newbie3", "password": "a-strong-password-123"},
            format="json",
        )

        assert response.status_code == 201

    @override_settings(ALLOW_REGISTRATION=False)
    def test_register_closed_returns_friendly_error(self, api_client):
        """★ 关掉注册后，接口要明确拒绝，而且**不能真的建出用户**。"""
        response = api_client.post(
            "/api/v1/auth/register",
            {"username": "uninvited", "password": "a-strong-password-123"},
            format="json",
        )

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "REGISTRATION_CLOSED"
        # 提示要告诉用户"下一步该怎么办"，不能只说"不允许"
        assert "管理员" in body["error"]["message"]
        # ★ 关键：不能建出账号
        assert not User.objects.filter(username="uninvited").exists()

    def test_daily_quota_exceeded_returns_chinese_message(
        self, auth_client, fake_search, fake_webpage, patch_model
    ):
        """★ 额度用完时返回 429 + 中文提示，而不是一堆英文。"""
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        with override_settings(AGENT_DAILY_QUOTA_PER_USER=1):
            first = auth_client.post(RUNS_URL, request_data(), format="json")
            assert first.status_code == 201

            second = auth_client.post(RUNS_URL, request_data(), format="json")

        assert second.status_code == 429
        body = second.json()
        assert body["success"] is False
        assert body["error"]["code"] == "DAILY_QUOTA_EXCEEDED"
        assert "每日上限" in body["error"]["message"]
        # 给用户看的话必须是中文，且不能漏出内部实现
        assert "Traceback" not in second.content.decode("utf-8")

    def test_rate_limit_message_is_chinese(self):
        """★ DRF 限流默认抛的是**英文**提示，必须在全局异常处理器里翻译掉。

        DRF 的 Throttled.default_detail 是 'Request was throttled.'，
        它会原样穿过我们自己的 envelope_exception_handler ——
        中文界面里蹦出一句英文，用户看不懂也不知道该等多久。

        这里直接调用异常处理器（而不是真去打满限流额度），
        是因为 DRF 的限流配置是启动时缓存住的，用 override_settings
        改 REST_FRAMEWORK 不会生效，得额外调 api_settings.reload()。
        直接测"翻译"这一段逻辑更准确，也更稳定。
        """
        from rest_framework.exceptions import Throttled

        from apps.common.exceptions import envelope_exception_handler

        response = envelope_exception_handler(Throttled(wait=3600), {"request": None})

        assert response.status_code == 429
        assert response.data["error"]["code"] == "TOO_MANY_REQUESTS"
        assert response.data["error"]["message"] == "操作太频繁了，请稍后再试。"
        # 不能把 DRF 的英文原文漏出去
        assert "throttled" not in str(response.data).lower()

    def test_throttle_scopes_are_wired_up(self):
        """确认限流真的挂上了，而不是"写了但没生效"。

        这类配置最容易静默失效：view 上忘了写 throttle_scope，
        ScopedRateThrottle 就会**直接放行**（它把"没有 scope"当成"不限制"），
        表现是一切正常，直到有人把额度刷光才被发现。
        """
        from apps.accounts.views import LoginView
        from apps.agent.views import AgentRunListCreateView

        rates = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        scoped_classes = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]

        assert "rest_framework.throttling.ScopedRateThrottle" in scoped_classes

        for view, expected_scope in (
            (AgentRunListCreateView, "agent_generate"),
            (LoginView, "login"),
        ):
            scope = getattr(view, "throttle_scope", None)
            assert scope == expected_scope, f"{view.__name__} 的 throttle_scope 丢了"
            assert scope in rates, f"scope {scope!r} 没有对应的限流速率配置"
