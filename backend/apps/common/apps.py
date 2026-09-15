from django.apps import AppConfig


class CommonConfig(AppConfig):
    """公共工具 app：统一响应、异常、分页、Markdown 渲染。

    这个 app 没有数据表，只是为了让 settings.INSTALLED_APPS 能引用它。
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
    verbose_name = "公共模块"
