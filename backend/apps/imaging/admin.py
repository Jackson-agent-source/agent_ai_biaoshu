"""
配图后台。

★ 图片改成文件存储之后，"BinaryField 渲染成巨大文本域把页面卡死"这个
  顾虑没有了，`image_file` 现在可以安全地放进 readonly_fields
  （admin 只会显示成一个指向 /media/... 的链接）。

★ 但**仍然不在 list_display 里放缩略图**：一页 100 行就是 100 个图片请求，
  列表页会变得很难用（理由从"数据库大字段"换成了"带宽+首屏"）。
  要逐张看图，用「预览」那一列点出去；详情页里有一张缩略图。
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
        # 文件字段只读：让用户能在后台看到文件路径、点开预览，
        # 但不允许手工改（改了会让 byte_size/width/height 和实际文件对不上）。
        "image_file",
    )
    date_hierarchy = "created_at"
    fieldsets = (
        ("归属", {"fields": ("id", "article", "user")}),
        ("内容", {"fields": ("suggestion_index", "suggestion", "prompt", "revised_prompt")}),
        ("状态", {"fields": ("status", "error_message")}),
        ("排版", {"fields": ("anchor_index", "sort_order", "is_cover", "caption")}),
        (
            "图片信息",
            {
                "fields": (
                    "image_file",
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
        """一个跳到图片地址的链接。

        用链接而不是 <img>：后台列表一页 100 行，
        每行都加载一张几百 KB 的图，页面会慢得没法用。
        """
        if not obj.image_file:
            return "-"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">查看</a>',
            obj.image_file.url,
        )
