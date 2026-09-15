"""Django Admin 配置 —— 方便在 /admin/ 后台直接查看数据。"""

from django.contrib import admin

from .models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "status", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("title", "user__username")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "message_type", "created_at")
    list_filter = ("role", "message_type")
    search_fields = ("content",)
    readonly_fields = ("created_at",)
