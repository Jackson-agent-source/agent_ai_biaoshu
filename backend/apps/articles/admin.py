"""文章 Admin —— 方便在 /admin/ 后台直接看和改文章。"""

from django.contrib import admin

from .models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "version", "model_name", "tool_call_count", "created_at")
    list_filter = ("status", "model_name", "created_at")
    search_fields = ("title", "summary", "user__username")
    readonly_fields = ("id", "content_html", "created_at", "updated_at", "version")
    date_hierarchy = "created_at"
    # 正文很长，单独分组显示更清爽
    fieldsets = (
        ("基本信息", {"fields": ("id", "user", "conversation", "agent_run", "title", "subtitle", "summary")}),
        ("内容", {"fields": ("hook", "outline", "content_markdown", "content_html")}),
        ("来源与风险", {"fields": ("source_ids", "risk_notes", "image_suggestions")}),
        ("状态", {"fields": ("status", "version", "model_name", "tool_call_count")}),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )
