"""本地隔离演示：python demo_trends.py（仅监听 127.0.0.1:8001）。

账号 trend_demo / DemoTrends2026!；示例热点和模型输出均为模拟数据。
数据库、媒体写入系统临时目录，退出后自动清理，不使用业务数据库。
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings_test"

import django
from django.conf import settings


def main():
    with tempfile.TemporaryDirectory(prefix="trend-web-demo-") as directory:
        settings.DATABASES["default"]["NAME"] = str(Path(directory) / "demo.sqlite3")
        settings.MEDIA_ROOT = str(Path(directory) / "media")
        settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
        settings.CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
        settings.SECURE_SSL_REDIRECT = False
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from apps.agent.tests.fakes import ScriptedChatModel, make_article_draft_message, make_tool_call_message, sample_draft_args
        from apps.agent.tools.web_search import SearchResponse, SearchResult
        from apps.trends.providers import TrendRecord

        call_command("migrate", verbosity=0)
        get_user_model().objects.create_user(username="trend_demo", password="DemoTrends2026!")
        records = [
            TrendRecord("google", "demo-ai", "【演示数据】AI Agent 如何改变工作流程", "https://example.org/demo/ai", "CN", 1),
            TrendRecord("google", "demo-sport", "【演示数据】城市足球赛事带动周末消费", "https://example.org/demo/sport", "CN", 2),
            TrendRecord("google", "demo-finance", "【演示数据】利率变化影响家庭理财选择", "https://example.org/demo/finance", "CN", 3),
        ]
        def model(**kwargs):
            return ScriptedChatModel(responses=[
                make_tool_call_message("web_search", {"query": "AI Agent 工作流程 演示"}),
                make_article_draft_message(sample_draft_args(
                    title="【演示草稿】AI Agent 怎样进入日常工作",
                    summary="本篇为功能演示，热点、搜索结果和模型输出均为模拟数据，用于展示从采集到文章生成的完整流程。",
                    body_markdown="## 从任务到流程\n\n这是一篇模拟生成的演示草稿。系统先采集热点，再建立可编辑的选题，最后通过文章生成服务保存内容。\n\n## 人工审核的作用\n\n实际使用时，需要核查来源、数据和关键结论。演示中没有调用真实模型，也没有发生对外发布。\n\n## 下一步\n\n你可以回到热点选题页面，查看该选题已经标记为已生成，并通过查看文章重新打开这份草稿。",
                    sources=[{"title": "演示资料", "url": "https://example.org/demo/reference", "usage": "仅展示来源追溯功能"}],
                    risk_notes=["演示数据，不可作为事实依据或发布内容。"],
                )),
            ])
        search = SimpleNamespace(name="demo", search=lambda *args, **kwargs: SearchResponse(results=[
            SearchResult(title="演示资料", url="https://example.org/demo/reference", snippet="模拟搜索结果，仅用于功能演示。"),
        ]))
        with (
            patch.dict("apps.trends.services.PROVIDERS", {"google": lambda: records}, clear=True),
            patch("apps.trends.services.fetch_focus_news", return_value=[
                TrendRecord("focus_news", "demo-focus", "【演示数据】人工智能助手开始协助多步骤任务", "https://example.org/demo/focus", "CN", 1),
            ]),
            patch("apps.trends.services.model_factory.is_model_configured", return_value=False),
            patch("apps.agent.agents.content_agent.get_chat_model", side_effect=model),
            patch("apps.agent.tools.web_search.get_search_provider", return_value=search),
        ):
            print("DEMO READY: http://127.0.0.1:8001 | trend_demo / DemoTrends2026!", flush=True)
            call_command("runserver", "127.0.0.1:8001", use_reloader=False)


if __name__ == "__main__":
    main()
