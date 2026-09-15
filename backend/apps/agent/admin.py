"""
Agent Admin —— 调试神器。

Agent 跑完之后如果结果不对，来这里看：
    1. AgentRun 里的 input_text（我们到底给模型发了什么）
    2. ToolCall 里它到底搜了什么、读了什么、成功没有
    3. Source 里它究竟拿到了哪些链接
这三个一看，问题基本就定位了。
"""

from django.contrib import admin

from .models import AgentRun, Source, ToolCall


class ToolCallInline(admin.TabularInline):
    model = ToolCall
    extra = 0
    fields = ("sequence", "tool_name", "arguments", "status", "duration_ms", "result_preview")
    readonly_fields = fields
    ordering = ("sequence",)
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "model_name", "tool_call_count", "created_at", "completed_at")
    list_filter = ("status", "model_name", "created_at")
    search_fields = ("id", "input_text", "conversation__user__username")
    readonly_fields = ("id", "created_at")
    inlines = [ToolCallInline]

    def has_add_permission(self, request) -> bool:
        # 运行记录由系统生成，不允许手动添加
        return False


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "agent_run", "status", "duration_ms", "sequence", "created_at")
    list_filter = ("tool_name", "status")
    search_fields = ("tool_name", "result_preview", "agent_run__id")
    readonly_fields = ("id", "created_at")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("title", "domain", "source_type", "is_cited", "published_at", "created_at")
    list_filter = ("source_type", "is_cited", "domain")
    search_fields = ("title", "url", "snippet")
    readonly_fields = ("id", "url_hash", "created_at")

    def has_add_permission(self, request) -> bool:
        return False
