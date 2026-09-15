"""
公众号推送后台。

★ 这是一张**日志表**，不是配置表：记录"哪篇文章、什么时候、推成功了没有、
  微信报了什么错"。所以整个后台是**只读**的 —— 允许改它等于允许伪造
  "我推成功了"，而实际微信那边什么都没有，是最坏的一种误导。
  只保留删除权限（清理历史用）。

★ content_html 必须 exclude：它是整篇 HTML，几十到几百 KB，
  塞进 admin 的表单会渲染成一个巨大的文本域，页面直接卡死
  （和当初 BinaryField 那个坑一模一样）。要看快照，
  用「正文快照」那一列点出去（走详情接口，admin 已登录所以 Session 认证有效）。
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.wechat.models import WeChatPublish


@admin.register(WeChatPublish)
class WeChatPublishAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "article",
        "status",
        "action",
        "image_count",
        "error_code",
        "created_at",
        "html_link",
    )
    list_filter = ("status", "action", "created_at")
    search_fields = ("id", "article__title", "media_id", "error_message")
    date_hierarchy = "created_at"
    # 整篇 HTML，不进表单（见模块注释）
    exclude = ("content_html",)
    readonly_fields = (
        "id",
        "article",
        "user",
        "status",
        "action",
        "media_id",
        "title",
        "image_count",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("归属", {"fields": ("id", "article", "user")}),
        ("结果", {"fields": ("status", "action", "media_id", "image_count")}),
        ("错误", {"fields": ("error_code", "error_message")}),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request) -> bool:
        """不允许手工新建 —— 推送记录只能由真实推送产生。"""
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """只读。保留删除权限（清理历史）。"""
        return False

    @admin.display(description="ID")
    def short_id(self, obj: WeChatPublish) -> str:
        """UUID 太长，列表里只显示前 8 位。"""
        return str(obj.id)[:8]

    @admin.display(description="正文快照")
    def html_link(self, obj: WeChatPublish) -> str:
        """一个跳到详情接口的链接（那里才返回 content_html）。"""
        if not obj.content_html:
            return "-"
        return format_html(
            '<a href="/api/v1/wechat-publishes/{}" target="_blank" rel="noopener">查看</a>',
            obj.id,
        )
