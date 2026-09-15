"""配图 App 的配置。写法与 apps/articles/apps.py 保持一致。"""

from __future__ import annotations

from django.apps import AppConfig


class ImagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imaging"
    # label 显式写出来：迁移文件里会用这个前缀建表（imaging_generatedimage），
    # 不写的话 Django 会按 name 的最后一段推断，这里恰好一样，
    # 但显式写出来以后改目录结构不会悄悄改表名。
    label = "imaging"
    verbose_name = "配图"
