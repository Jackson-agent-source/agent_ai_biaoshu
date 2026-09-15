from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """账号模块。

    V1 直接使用 Django 自带的 User 表和认证系统，不自己重造用户体系。
    这里只提供登录/登出/查询当前用户的 API 给前端用。
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "账号"
