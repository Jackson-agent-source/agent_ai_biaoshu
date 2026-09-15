"""微信公众号 App 的配置。"""

from __future__ import annotations

from django.apps import AppConfig


class WeChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wechat"
    label = "wechat"
    verbose_name = "微信公众号"
