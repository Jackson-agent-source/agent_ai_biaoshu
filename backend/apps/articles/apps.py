from django.apps import AppConfig


class ArticlesConfig(AppConfig):
    """最终文章。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.articles"
    label = "articles"
    verbose_name = "文章"
