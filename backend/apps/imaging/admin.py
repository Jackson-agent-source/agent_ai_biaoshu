"""
配图后台。

★ 这里**不显示图片本体**（不把 data 塞进 list_display 或 readonly_fields）。
  Django admin 会把 BinaryField 渲染成一个巨大的文本域，几百 KB 的二进制
  变成一个几百万字符的输入框，页面直接卡死。
  要看图，用「预览」那一列点出去（走 /api/v1/images/<id>/file）。
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.imaging.models import GeneratedImage


@admin.register(GeneratedImage)
class GeneratedImageAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "article",
        "status",
        "anchor_index",
        "is_cover",
        "byte_size_display",
        "model_name",
        "duration_ms",
        "created_at",
        "preview",
    )
    list_filter = ("status", "is_cover", "provider", "model_name", "created_at")
    search_fields = ("id", "suggestion", "prompt", "caption", "article__title")
    readonly_fields = (
        "id",
        "provider",
        "model_name",
        "duration_ms",
        "byte_size",
        "width",
        "height",
        "content_type",
        "wechat_url",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    # 二进制字段不进表单
    exclude = ("data",)
    fieldsets = (
        ("归属", {"fields": ("id", "article", "user")}),
        ("内容", {"fields": ("suggestion_index", "suggestion", "prompt", "revised_prompt")}),
        ("状态", {"fields": ("status", "error_message")}),
        ("排版", {"fields": ("anchor_index", "sort_order", "is_cover", "caption")}),
        (
            "图片信息",
            {
                "fields": (
                    "content_type",
                    "width",
                    "height",
                    "byte_size",
                    "provider",
                    "model_name",
                    "duration_ms",
                    "wechat_url",
                )
            },
        ),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="ID")
    def short_id(self, obj: GeneratedImage) -> str:
        """UUID 太长，列表里只显示前 8 位。"""
        return str(obj.id)[:8]

    @admin.display(description="大小")
    def byte_size_display(self, obj: GeneratedImage) -> str:
        if not obj.byte_size:
            return "-"
        if obj.byte_size >= 1024 * 1024:
            return f"{obj.byte_size / 1024 / 1024:.1f} MB"
        return f"{obj.byte_size / 1024:.0f} KB"

    @admin.display(description="预览")
    def preview(self, obj: GeneratedImage) -> str:
        """一个跳到取图接口的链接。

        用链接而不是 <img>：后台列表一页 100 行，
        每行都加载一张几百 KB 的图，页面会慢得没法用。
        """
        if not obj.data:
            return "-"
        return format_html(
            '<a href="/api/v1/images/{}/file" target="_blank" rel="noopener">查看</a>',
            obj.id,
        )
