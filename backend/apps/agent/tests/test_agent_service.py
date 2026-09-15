"""
AgentService 的端到端测试。

和其他测试不同，这里**不 mock Agent 本身** —— 我们用假模型驱动真实的
LangChain Agent 循环，跑真实的工具、真实的 SourceRegistry、真实的数据库写入。
只有两个东西是假的：
    1. 大模型（避免花钱、避免不稳定）
    2. 搜索服务（避免联网）

假数据和 fixture 都放在同目录的 conftest.py 里，这里直接用。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings as dj_settings
from django.test import override_settings
from django.utils import timezone
from langchain_core.messages import AIMessage, ToolMessage

from apps.agent.models import AgentRun, Source, ToolCall
from apps.agent.services.agent_service import AgentService
from apps.agent.tests.conftest import SEARCH_RESULTS, request_data
from apps.agent.tests.fakes import (
    make_article_draft_message,
    make_tool_call_message,
    sample_draft_args,
)
from apps.agent.tools.web_search import SearchResponse
from apps.articles.models import Article
from apps.common.exceptions import (
    AgentExecutionError,
    DailyQuotaExceededError,
    StructuredOutputError,
)
from apps.conversations.models import Conversation, Message


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestGenerateArticleSuccess:
    def test_full_flow_creates_article(self, user, fake_search, fake_webpage, patch_model):
        """完整链路：搜索 -> 读网页 -> 写文章 -> 落库。

        对应需求文档场景 B。
        """
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent 进展", "max_results": 5}, "c1"),
                make_tool_call_message("read_webpage", {"url": "https://real-report.com/2026-ai-agent"}, "c2"),
                make_article_draft_message(
                    sample_draft_args(
                        title="AI Agent 正在进入生产环境",
                        sources=[
                            {
                                "title": "2026 年 AI Agent 行业报告",
                                "url": "https://real-report.com/2026-ai-agent",
                                "usage": "提供了智能体进入生产环境的情况",
                            }
                        ],
                        risk_notes=["部分数据来自单一来源，未做交叉验证。"],
                    )
                ),
            ]
        )

        article, agent_run = AgentService.generate_article(
            user=user, request_data=request_data()
        )

        # ---- 1. Article 正确落库 ----
        assert isinstance(article, Article)
        assert article.title == "AI Agent 正在进入生产环境"
        assert article.status == Article.Status.DRAFT  # ★ V1 永远只生成草稿
        assert article.user == user
        assert article.content_markdown.startswith("## ")
        assert "<h2>" in article.content_html  # Markdown 已渲染成 HTML
        assert article.risk_notes  # 模型的风险提示 + 系统的提示都在

        # ---- 2. AgentRun 状态正确 ----
        assert agent_run.status == AgentRun.Status.COMPLETED
        assert agent_run.started_at is not None
        assert agent_run.completed_at is not None
        assert agent_run.tool_call_count == 2
        assert agent_run.input_text  # 我们给模型的完整输入有留档
        assert "2026 年 AI Agent 有哪些新进展" in agent_run.input_text

        # ---- 3. ToolCall 有记录，且顺序、耗时、参数都对 ----
        tool_calls = list(ToolCall.objects.filter(agent_run=agent_run).order_by("sequence"))
        assert len(tool_calls) == 2
        assert tool_calls[0].tool_name == "web_search"
        assert tool_calls[0].arguments["query"] == "2026 AI Agent 进展"
        assert tool_calls[0].status == ToolCall.Status.SUCCESS
        assert tool_calls[0].duration_ms is not None
        assert tool_calls[1].tool_name == "read_webpage"
        assert tool_calls[1].arguments["url"] == "https://real-report.com/2026-ai-agent"

        # ---- 4. Source 有记录 ----
        sources = Source.objects.filter(agent_run=agent_run)
        assert sources.count() == 2
        cited = sources.filter(is_cited=True)
        assert cited.count() == 1
        assert cited.first().usage == "提供了智能体进入生产环境的情况"

        # ---- 5. Message 有记录（用户 + Agent 各一条）----
        messages = Message.objects.filter(conversation=agent_run.conversation)
        assert messages.count() == 2
        assert messages.filter(role=Message.Role.USER).exists()
        assistant_msg = messages.get(role=Message.Role.ASSISTANT)
        assert assistant_msg.message_type == Message.MessageType.ARTICLE
        assert assistant_msg.metadata["article_id"] == str(article.id)

        # ---- 6. 会话被创建 ----
        assert Conversation.objects.filter(user=user, id=agent_run.conversation_id).exists()

    def test_tool_call_result_preview_is_truncated(self, user, fake_search, fake_webpage, patch_model):
        """ToolCall.result_preview 只存摘要，不存第三方网页全文。"""
        patch_model(
            [
                make_tool_call_message("read_webpage", {"url": "https://real-report.com/2026-ai-agent"}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        _article, agent_run = AgentService.generate_article(user=user, request_data=request_data())

        call = ToolCall.objects.get(agent_run=agent_run, tool_name="read_webpage")
        max_chars = dj_settings.AGENT_LIMITS["RESULT_PREVIEW_CHARS"]
        assert len(call.result_preview) <= max_chars + 20  # 允许"...(已截断)"后缀
        # 不应该包含整篇网页正文
        assert "与此同时，可靠性与成本" not in call.result_preview

    def test_no_tools_used_when_research_disabled(self, user, patch_model):
        """用户关闭联网研究：模型不调用工具，也不该产生"资料不足"的提示。"""
        patch_model([make_article_draft_message(sample_draft_args())])

        article, agent_run = AgentService.generate_article(
            user=user,
            request_data=request_data(research_mode=False),
        )

        assert ToolCall.objects.filter(agent_run=agent_run).count() == 0
        assert agent_run.tool_call_count == 0
        # research_mode=False 时不该出现"未能获取外部资料"这类提示
        assert not any("未能获取到任何外部资料" in note for note in article.risk_notes)


# ---------------------------------------------------------------------------
# 防幻觉：伪造来源必须被剔除（场景 C）
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSourceValidation:
    def test_forged_source_is_removed(self, user, fake_search, patch_model):
        """★ 需求文档场景 C：

        模型返回了 https://fake-example-123456.com，
        但搜索工具从没找到过这个 URL —— 系统必须拒绝保存它。
        """
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent", "max_results": 5}, "c1"),
                make_article_draft_message(
                    sample_draft_args(
                        sources=[
                            {
                                "title": "真实来源",
                                "url": "https://real-report.com/2026-ai-agent",
                                "usage": "提供了行业情况",
                            },
                            {
                                "title": "编造出来的来源",
                                "url": "https://fake-example-123456.com/article",
                                "usage": "提供了某个惊人的数字",
                            },
                        ]
                    )
                ),
            ]
        )

        article, agent_run = AgentService.generate_article(user=user, request_data=request_data())

        # ---- 假来源不能出现在最终文章里 ----
        assert len(article.source_ids) == 1
        cited = Source.objects.filter(id__in=article.source_ids).first()
        assert cited.url == "https://real-report.com/2026-ai-agent"
        assert "fake-example-123456.com" not in str(article.source_ids)

        # ---- 数据库里也不该有这条假来源 ----
        assert not Source.objects.filter(agent_run=agent_run, url__contains="fake-example-123456").exists()

        # ---- 必须留下风险提示 ----
        assert any("未经检索验证" in note for note in article.risk_notes)

    def test_all_forged_sources_leaves_empty_list(self, user, fake_search, patch_model):
        """全部是编造的：文章一条来源都不该有，但文章本身仍然生成成功。"""
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(
                    sample_draft_args(
                        sources=[
                            {"title": "假 A", "url": "https://totally-fake-a.com/1", "usage": "x"},
                            {"title": "假 B", "url": "https://totally-fake-b.com/2", "usage": "y"},
                        ]
                    )
                ),
            ]
        )

        article, _run = AgentService.generate_article(user=user, request_data=request_data())

        assert article.source_ids == []
        assert any("未经检索验证" in note for note in article.risk_notes)

    def test_source_reused_across_runs_is_still_validated(self, user, fake_search, patch_model):
        """上一次运行搜到的 URL，在这一次运行时不算数 —— 必须重新出现在本次检索中。"""
        # 第一次运行：搜到 real-report.com
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )
        AgentService.generate_article(user=user, request_data=request_data())

        # 第二次运行：模型不搜索，直接引用上次的 URL
        patch_model(
            [
                make_article_draft_message(
                    sample_draft_args(
                        sources=[
                            {"title": "上次的来源", "url": "https://real-report.com/2026-ai-agent", "usage": "x"}
                        ]
                    )
                )
            ]
        )
        article, _run = AgentService.generate_article(user=user, request_data=request_data())

        # 这次没搜索，登记簿是空的，所以这个 URL 无效
        assert article.source_ids == []


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestFailureHandling:
    def test_llm_failure_marks_run_failed(self, user, mocker):
        """模型调用失败：AgentRun.status == failed，且不返回敏感信息。"""
        mocker.patch(
            "apps.agent.agents.content_agent.get_chat_model",
            side_effect=RuntimeError("Connection error: api key sk-secret-12345 rejected"),
        )

        with pytest.raises(AgentExecutionError) as exc_info:
            AgentService.generate_article(user=user, request_data=request_data())

        agent_run = AgentRun.objects.latest("created_at")
        assert agent_run.status == AgentRun.Status.FAILED
        assert agent_run.completed_at is not None
        assert agent_run.error_message  # 有给用户看的中文说明

        # ★ 绝不能把 API Key 泄漏给前端
        user_facing = f"{exc_info.value.message} {agent_run.error_message}"
        assert "sk-secret-12345" not in user_facing
        assert "Traceback" not in user_facing

        # 也不该生成文章
        assert Article.objects.filter(user=user).count() == 0

    def test_missing_structured_output_marks_failed(self, user, patch_model):
        """模型只输出正文、补救也救不回来时：抛 StructuredOutputError，运行标记失败。

        剧本第 1 条是普通文本（触发补救），第 2 条剧本用完了、仍是普通文本
        （补救失败），所以最终还是要报错。
        """
        patch_model([AIMessage(content="我写好了，但忘了用结构化格式返回。")])

        with pytest.raises(StructuredOutputError):
            AgentService.generate_article(user=user, request_data=request_data())

        agent_run = AgentRun.objects.latest("created_at")
        assert agent_run.status == AgentRun.Status.FAILED
        assert Article.objects.filter(user=user).count() == 0

    def test_model_writing_plain_text_gets_recovered(self, user, patch_model):
        """★ 回归测试：模型把文章写成普通正文时，要能救回来。

        这是网页上真实遇到过的失败 —— 用户在页面上看到
        「模型没有返回结构化的文章内容」，但模型其实**已经把文章写完了**，
        只是没调用 ArticleDraft 工具，写在了普通消息里。

        失败现场：33 秒、0 条工具调用记录、报结构化输出错误。
        真拿原始输入复现又复现不出来 —— 它是偶发的，取决于模型当次的心情。

        假模型反而是测这个的理想工具：让它第一条返回正文、第二条返回
        ArticleDraft，就能稳定地重放这个场景，而且不用花钱、不用碰运气。
        """
        model = patch_model(
            [
                AIMessage(
                    content=(
                        "## 聊天机器人会说话，智能体会动手\n\n"
                        "这是一篇已经写好的正文，但模型忘了调用 ArticleDraft 工具。"
                    )
                ),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        article, agent_run = AgentService.generate_article(
            user=user, request_data=request_data()
        )

        # 补救之后应该正常出稿，而不是报错
        assert agent_run.status == AgentRun.Status.COMPLETED
        assert article.status == Article.Status.DRAFT
        assert article.title == sample_draft_args()["title"]

        # 确实调用了两次模型：第一次写正文，第二次被点醒后改用工具返回
        assert len(model.calls) == 2
        # 第二次调用里必须带上那句"你已经写完了，别再重写"的提示，
        # 否则模型会从头再检索一遍，白花一次钱和时间
        second_call_text = " ".join(
            m.content for m in model.calls[1] if isinstance(m.content, str)
        )
        assert "不要重新搜索" in second_call_text
        assert "ArticleDraft" in second_call_text

    def test_failed_run_still_records_what_already_happened(
        self, user, fake_search, patch_model
    ):
        """★ 回归测试：就算生成失败，已经发生的工具调用和来源也必须留档。

        这是修 bug 时发现的另一个坑：写 ToolCall 的代码在 Agent 调用**之后**，
        一旦 Agent 抛异常就直接跳过了，记录全丢。结果是失败运行的时间线一片空白 ——
        用户不知道研究走到了哪一步，我们也拿不到现场，只能靠猜。
        上一轮排查那个偶发失败时就吃了这个亏。
        """
        # 先正常搜一次，然后模型交出正文却又救不回来 —— 整次运行判定失败
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "搜一下", "max_results": 5}, "c1"),
                AIMessage(content="文章我写在正文里了，但没调用工具。"),
            ]
        )

        with pytest.raises(StructuredOutputError):
            AgentService.generate_article(user=user, request_data=request_data())

        agent_run = AgentRun.objects.latest("created_at")
        assert agent_run.status == AgentRun.Status.FAILED

        # ★ 关键：失败归失败，那次搜索的记录必须还在
        calls = list(ToolCall.objects.filter(agent_run=agent_run))
        assert len(calls) == 1
        assert calls[0].tool_name == "web_search"
        assert calls[0].status == ToolCall.Status.SUCCESS
        # 搜到的来源也要留下
        assert Source.objects.filter(agent_run=agent_run).count() == len(SEARCH_RESULTS)

        # 而且失败运行不该产出文章
        assert Article.objects.filter(user=user).count() == 0

    def test_invalid_model_config_marks_failed(self, user, mocker):
        """没有配置 LLM_API_KEY：给用户一句能看懂的中文提示。"""
        from apps.common.exceptions import ModelProviderError

        mocker.patch(
            "apps.agent.agents.content_agent.get_chat_model",
            side_effect=ModelProviderError("还没有配置 LLM_API_KEY，请在后端 .env 文件里填写你的 API Key。"),
        )

        with pytest.raises(ModelProviderError) as exc_info:
            AgentService.generate_article(user=user, request_data=request_data())

        assert "LLM_API_KEY" in exc_info.value.message
        assert AgentRun.objects.latest("created_at").status == AgentRun.Status.FAILED

    def test_search_failure_does_not_crash(self, user, patch_model, mocker):
        """★ 需求文档场景 E：搜索服务挂了，整个流程不能崩。

        Agent 应该基于有限信息把文章写出来，并在风险提示里说明。
        """
        provider = mocker.Mock()
        provider.name = "tavily"
        provider.search.return_value = SearchResponse(
            error="search_provider_timeout", message="搜索服务响应超时。"
        )
        mocker.patch("apps.agent.tools.web_search.get_search_provider", return_value=provider)

        patch_model(
            [
                make_tool_call_message("web_search", {"query": "2026 AI Agent", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        article, agent_run = AgentService.generate_article(user=user, request_data=request_data())

        # 服务没有崩，文章照样生成
        assert article.status == Article.Status.DRAFT
        assert agent_run.status == AgentRun.Status.COMPLETED

        # 失败的工具调用被如实记录
        call = ToolCall.objects.get(agent_run=agent_run)
        assert call.status == ToolCall.Status.FAILED
        assert call.result_preview  # 有记录失败原因

        # 风险提示里要说明资料检索出了问题
        joined = " ".join(article.risk_notes)
        assert "工具调用失败" in joined or "未能获取到任何外部资料" in joined

    def test_unexpected_exception_does_not_leak_details(self, user, mocker):
        """非预期异常：用户只看到通用提示，堆栈只进日志。"""
        mocker.patch(
            "apps.agent.agents.content_agent.get_chat_model",
            side_effect=ValueError("内部数据库密码是 hunter2"),
        )

        with pytest.raises(AgentExecutionError) as exc_info:
            AgentService.generate_article(user=user, request_data=request_data())

        agent_run = AgentRun.objects.latest("created_at")
        assert "hunter2" not in exc_info.value.message
        assert "hunter2" not in agent_run.error_message
        assert "Traceback" not in agent_run.error_message
        # 给用户看到的是中文提示，不是原始异常字符串
        assert agent_run.error_message.endswith("。")


# ---------------------------------------------------------------------------
# 防失控：工具调用次数上限
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestToolCallLimit:
    def test_exceeding_limit_stops_research_and_adds_risk_note(
        self, user, fake_search, patch_model
    ):
        """超过 MAX_TOOL_CALLS 后：停止研究，但仍然产出文章 + 风险提示。

        这里把上限压到 1，然后让模型连续请求 3 次搜索。
        """
        limited = {**dj_settings.AGENT_LIMITS, "MAX_TOOL_CALLS": 1}

        with override_settings(AGENT_LIMITS=limited):
            patch_model(
                [
                    make_tool_call_message("web_search", {"query": "第一次搜索", "max_results": 5}, "c1"),
                    make_tool_call_message("web_search", {"query": "第二次搜索", "max_results": 5}, "c2"),
                    make_tool_call_message("web_search", {"query": "第三次搜索", "max_results": 5}, "c3"),
                    make_article_draft_message(sample_draft_args()),
                ]
            )

            article, agent_run = AgentService.generate_article(
                user=user, request_data=request_data()
            )

        # 只有 1 次真的执行了 —— 这是用户看到的"工具调用次数"
        assert agent_run.tool_call_count == 1

        # 但三次请求都留了痕：1 条成功 + 2 条被预算拦下（blocked）。
        # 拦下的也要记，否则时间线上会莫名其妙少两步，用户不知道研究为什么断了。
        calls = list(ToolCall.objects.filter(agent_run=agent_run).order_by("sequence"))
        assert [c.status for c in calls] == ["success", "blocked", "blocked"]
        # 被拦下的调用必须解释清楚原因，且**不能**记成失败
        assert all("调用上限" in c.error_message for c in calls[1:])

        # 但文章仍然生成成功（这是关键 —— 不能因为超限就整个失败）
        assert article.status == Article.Status.DRAFT
        assert agent_run.status == AgentRun.Status.COMPLETED

        # 必须告诉用户研究被中断了
        assert any("达到研究调用上限" in note for note in article.risk_notes)

        # ★ 而且不能因此多报一条"工具调用失败" —— 被预算拦下不等于工具坏了
        assert not any("工具调用失败" in note for note in article.risk_notes), article.risk_notes

    def test_zero_limit_blocks_all_research_but_still_writes(self, user, patch_model):
        """把上限配成 0 = 这次完全不做外部研究。

        第一次检索就该被拦下，但文章照样要写出来，并如实说明"没有外部资料"。
        """
        limited = {**dj_settings.AGENT_LIMITS, "MAX_TOOL_CALLS": 0}

        with override_settings(AGENT_LIMITS=limited):
            patch_model(
                [
                    make_tool_call_message("web_search", {"query": "随便搜搜", "max_results": 5}, "c1"),
                    make_article_draft_message(sample_draft_args()),
                ]
            )

            article, agent_run = AgentService.generate_article(
                user=user, request_data=request_data()
            )

        assert agent_run.tool_call_count == 0
        assert agent_run.status == AgentRun.Status.COMPLETED
        assert article.status == Article.Status.DRAFT
        # 一次外部资料都没拿到，必须说出来，不能让用户以为文章有依据
        assert any("未能获取到任何外部资料" in note for note in article.risk_notes), article.risk_notes

    def test_blocked_notice_tells_model_what_to_do_not_just_what_not_to_do(
        self, user, fake_search, patch_model
    ):
        """★ 回归测试：拦截话术必须告诉模型"接下来该做什么"。

        这是踩过的真坑，必须钉死在测试里：

            LangChain 自带的 ToolCallLimitMiddleware 拦截时会注入
                "Tool call limit exceeded. Do not make additional tool calls."
            模型把这句话理解成"那我什么都不做了" —— 可对模型来说
            "返回文章"**本来也是一次工具调用**（ArticleDraft）。
            于是它回一条空消息、循环结束、structured_response 是 None、
            整次生成判定失败，用户什么都拿不到。

        有意思的是：假模型测试**抓不到**这个 bug，因为剧本是硬编码的，
        模型没有判断力、不会"被吓住"。所以这里换个角度守 ——
        不去模拟模型的反应，而是直接断言我们喂给模型的那句话
        里**同时**有"别检索了"和"现在去出稿"两层意思。
        只要这句话还在，模型就有明确的下一步可走。
        """
        limited = {**dj_settings.AGENT_LIMITS, "MAX_TOOL_CALLS": 1}

        with override_settings(AGENT_LIMITS=limited):
            model = patch_model(
                [
                    make_tool_call_message("web_search", {"query": "第一次", "max_results": 5}, "c1"),
                    make_tool_call_message("web_search", {"query": "第二次", "max_results": 5}, "c2"),
                    make_article_draft_message(sample_draft_args()),
                ]
            )

            AgentService.generate_article(user=user, request_data=request_data())

        # 从假模型记录的调用历史里，把被拦截时那条 ToolMessage 捞出来
        intercepted = [
            message.content
            for call in model.calls
            for message in call
            if isinstance(message, ToolMessage) and message.tool_call_id == "c2"
        ]
        assert len(intercepted) == 1, "被拦下的调用应该正好回给模型一条提示"
        notice = intercepted[0]

        # 第一层：说清楚"别再检索了"
        assert "上限" in notice
        # 第二层（关键）：明确告诉它下一步是**出稿**，不是"什么都别做"
        assert "输出最终文章" in notice
        # 不能说成是出错了，否则模型可能去"修复问题"而不是收尾
        assert "不是出错" in notice
        # 而且要提示把资料缺口写进风险说明，不能假装研究很充分
        assert "risk_notes" in notice


# ---------------------------------------------------------------------------
# 内部方法
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestInternalHelpers:
    def test_conversation_reused_when_provided(self, user, fake_search, patch_model):
        """传了 conversation 就复用它，而不是每次都新建。"""
        conversation = Conversation.objects.create(user=user, title="已有会话")
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(sample_draft_args()),
            ]
        )

        _article, agent_run = AgentService.generate_article(
            user=user, request_data=request_data(), conversation=conversation
        )

        assert agent_run.conversation_id == conversation.id
        assert Conversation.objects.filter(user=user).count() == 1

    def test_get_or_create_conversation_blocks_other_users(self, user, other_user):
        """★ 越权防护：不能往别人的会话里写东西。"""
        from apps.agent.services.agent_service import get_or_create_conversation

        other_conversation = Conversation.objects.create(user=other_user, title="别人的会话")

        assert get_or_create_conversation(user=user, conversation_id=other_conversation.id) is None
        assert get_or_create_conversation(user=user, conversation_id=None) is None

    def test_markdown_rendered_and_sanitized(self, user, fake_search, patch_model):
        """正文里的 <script> 必须被清掉，防 XSS。"""
        patch_model(
            [
                make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
                make_article_draft_message(
                    sample_draft_args(
                        body_markdown="## 标题\n\n正常内容。\n\n<script>alert('xss')</script>\n\n"
                    )
                ),
            ]
        )

        article, _run = AgentService.generate_article(user=user, request_data=request_data())

        assert "<script>" not in article.content_html
        assert "alert(" not in article.content_html
        assert "正常内容" in article.content_html


# ===========================================================================
# 每日额度（防烧钱）
# ===========================================================================
@pytest.mark.django_db
class TestDailyQuota:
    """每日生成额度的行为。

    这一层的意义：DRF 的频率限流管的是"单位时间打了几次"，
    但把大模型额度刷光最典型的姿势是"每天正常用，每次都跑满工具调用" ——
    频率完全正常，只有自然日的总量账本挡得住。
    """

    # 一次成功生成需要的剧本：搜一次 -> 返回结构化文章
    SCRIPT = [
        make_tool_call_message("web_search", {"query": "x", "max_results": 5}, "c1"),
        make_article_draft_message(sample_draft_args()),
    ]

    @staticmethod
    def _generate(user, patch_model):
        """生成一次。

        ★ 每次都要重新 patch_model：假模型的"剧本"是一次性的，
          第一次调用就把剧本用完了，第二次调用只会拿到一条普通文本消息、
          然后因为缺少结构化输出而报错。这跟额度逻辑无关，
          纯粹是假模型的特性，所以每次生成前都要重新装一份剧本。
        """
        patch_model(TestDailyQuota.SCRIPT)
        return AgentService.generate_article(user=user, request_data=request_data())

    def test_quota_disabled_by_default(self, user, fake_search, patch_model):
        """默认 0 = 不限制，本地开发和测试不受影响。"""
        for _ in range(2):
            self._generate(user, patch_model)

        assert AgentRun.objects.filter(conversation__user=user).count() == 2

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=1)
    def test_per_user_quota_blocks_second_run(self, user, fake_search, patch_model):
        self._generate(user, patch_model)

        with pytest.raises(DailyQuotaExceededError) as exc:
            self._generate(user, patch_model)

        # 提示要能说清"为什么不行、怎么办"，不能只说"额度超了"
        assert "每日上限" in exc.value.message
        assert exc.value.status_code == 429
        assert exc.value.code == "DAILY_QUOTA_EXCEEDED"

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=1)
    def test_quota_blocks_before_calling_the_model(
        self, user, fake_search, patch_model
    ):
        """★ 额度检查必须在**调用大模型之前**。

        这条是防烧钱的关键：如果先调模型再检查额度，钱已经花了。
        所以这里故意**不装剧本** —— 一旦代码真的走到模型那一步，
        patch_model 没被调用，就会去连真实的 DeepSeek，
        而测试环境没有 Key，会立刻炸。测试失败即证明拦截位置错了。
        """
        self._generate(user, patch_model)

        # 第二次不装剧本：额度检查应该在这之前就把请求挡下来
        with pytest.raises(DailyQuotaExceededError):
            AgentService.generate_article(user=user, request_data=request_data())

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=1)
    def test_quota_blocks_before_creating_anything(self, user, fake_search, patch_model):
        """★ 额度用尽时不能产生任何副作用。

        检查放在最前面（连 Conversation 都还没建）就是为了这个：
        额度用完的请求不该在库里留下半个会话、半个 AgentRun。
        """
        self._generate(user, patch_model)

        conversations_before = Conversation.objects.count()
        runs_before = AgentRun.objects.count()

        with pytest.raises(DailyQuotaExceededError):
            self._generate(user, patch_model)

        assert Conversation.objects.count() == conversations_before
        assert AgentRun.objects.count() == runs_before

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=3)
    def test_quota_is_per_user_not_global(self, user, other_user, fake_search, patch_model):
        """★ 额度是按人算的。一个人用满了，不该影响另一个人。

        这条防的是"把 conversation__user 过滤写掉"这类错误 ——
        写成全站计数的话，第一个用户就能把所有人的额度吃掉。
        """
        for _ in range(3):
            self._generate(user, patch_model)

        # user 已经用满 3 篇
        with pytest.raises(DailyQuotaExceededError):
            self._generate(user, patch_model)

        # other_user 一次都没用过，必须还能正常生成
        self._generate(other_user, patch_model)
        assert AgentRun.objects.filter(conversation__user=other_user).count() == 1

    @override_settings(AGENT_DAILY_QUOTA_GLOBAL=2, AGENT_DAILY_QUOTA_PER_USER=0)
    def test_global_cap_counts_everyone(self, user, other_user, fake_search, patch_model):
        """全站总额度：所有人共享，用完谁都别想再生成。"""
        self._generate(user, patch_model)
        self._generate(other_user, patch_model)

        with pytest.raises(DailyQuotaExceededError) as exc:
            self._generate(user, patch_model)

        assert "全站" in exc.value.message

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=1)
    def test_quota_resets_at_local_midnight(self, user, fake_search, patch_model):
        """★ 跨零点必须重置，而且要按**上海时区**的零点算。

        这是个很容易写错、又很难发现的 bug：
        created_at 存的是 UTC（USE_TZ=True），如果直接拿 UTC 的零点当分界，
        中国用户会在北京时间**早上 8 点**才重置额度，而不是零点。

        这里把已有的 AgentRun 的 created_at 改成"北京时间昨天 23:59"，
        额度就应该已经重置了 —— 如果实现用的是 UTC 零点，
        这个时刻换算过去是前一天的 UTC 15:59，仍然落在"今天"，测试就会失败。
        """
        self._generate(user, patch_model)

        # 把这次运行挪到"上海时区的昨天 23:59"
        yesterday_late = timezone.localtime().replace(
            hour=23, minute=59, second=0, microsecond=0
        ) - timedelta(days=1)
        AgentRun.objects.filter(conversation__user=user).update(created_at=yesterday_late)

        # 昨天用掉的额度不该算进今天
        self._generate(user, patch_model)

        assert AgentRun.objects.filter(conversation__user=user).count() == 2

    @override_settings(AGENT_DAILY_QUOTA_PER_USER=1)
    def test_failed_run_still_consumes_quota(self, user, patch_model):
        """★ 失败的运行**同样**占额度。

        失败的那次照样真实调用了大模型、照样花钱。只按成功计数的话，
        等于给失败留了个免费刷的洞 —— 反复触发失败就能绕开额度。
        """
        # 模型只回正文、不调结构化输出工具，补救也救不回来 -> 这次生成会失败
        patch_model([AIMessage(content="我写好了，但忘了用结构化格式返回。")])

        with pytest.raises(StructuredOutputError):
            AgentService.generate_article(user=user, request_data=request_data())

        # AgentRun 是 failed 状态，但它确实花掉了一次额度
        assert AgentRun.objects.filter(
            conversation__user=user, status=AgentRun.Status.FAILED
        ).count() == 1

        with pytest.raises(DailyQuotaExceededError):
            AgentService.generate_article(user=user, request_data=request_data())
