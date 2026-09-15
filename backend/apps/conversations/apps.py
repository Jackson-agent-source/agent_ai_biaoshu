from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    """会话与消息。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.conversations"
    label = "conversations"
    verbose_name = "会话与消息"
