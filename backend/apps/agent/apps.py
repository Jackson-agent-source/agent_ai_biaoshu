from django.apps import AppConfig


class AgentConfig(AppConfig):
    """Agent 执行记录：AgentRun / ToolCall / Source。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agent"
    label = "agent"
    verbose_name = "Agent 运行记录"
