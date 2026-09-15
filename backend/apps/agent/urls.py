"""Agent 接口路由。全部挂在 /api/v1/agent/ 下面。"""

from django.urls import path

from apps.agent import views

app_name = "agent"

urlpatterns = [
    # 创建 / 列出 Agent 运行
    path("runs", views.AgentRunListCreateView.as_view(), name="run-list-create"),
    # 单次运行的详情
    path("runs/<uuid:run_id>", views.AgentRunDetailView.as_view(), name="run-detail"),
    # 工具调用过程（前端时间线）
    path("runs/<uuid:run_id>/tool-calls", views.AgentRunToolCallsView.as_view(), name="run-tool-calls"),
    # 这次运行检索到的来源
    path("runs/<uuid:run_id>/sources", views.AgentRunSourcesView.as_view(), name="run-sources"),
    # 会话
    path("conversations", views.ConversationListView.as_view(), name="conversation-list"),
    path(
        "conversations/<uuid:conversation_id>/messages",
        views.ConversationMessagesView.as_view(),
        name="conversation-messages",
    ),
]
