"""
公众号推送记录。

为什么每一篇都要存一条记录、而不是在 Article 上放两个字段（media_id / pushed_at）：

    1. 用户会**反复推**："改一版 → 推一次 → 再看看 → 再改"。
       只存最后一次的话，"上次到底推没推成功、报了什么错"就查不到了。
    2. 微信的错误信息很有价值（40164 白名单、48001 没权限…），
       存下来才能在界面上直接告诉用户上次为什么失败。
    3. 存了 media_id 才能实现"再推一次是更新同一份草稿，不是新建一份"。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class WeChatPublish(models.Model):
    """一次"推送到公众号草稿箱"的记录。"""

    class Status(models.TextChoices):
        RUNNING = "running", "推送中"
        SUCCEEDED = "succeeded", "已创建草稿"
        FAILED = "failed", "失败"

    class Action(models.TextChoices):
        ADD = "add", "新建草稿"
        UPDATE = "update", "更新草稿"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    article = models.ForeignKey(
        "articles.Article",
        on_delete=models.CASCADE,
        related_name="wechat_publishes",
        verbose_name="文章",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wechat_publishes",
        verbose_name="操作人",
    )

    status = models.CharField(
        "状态", max_length=20, choices=Status.choices, default=Status.RUNNING, db_index=True
    )
    action = models.CharField(
        "动作", max_length=10, choices=Action.choices, blank=True, default=""
    )

    # 微信草稿的 media_id。★ 下次推送要靠它走 draft/update，
    # 所以只有成功的那次才写。
    media_id = models.CharField("草稿 media_id", max_length=200, blank=True, default="")

    # 本次推送实际送出去的 HTML 快照。
    # ★ 为什么要存：公众号渲染和浏览器差得很远，用户说"排版乱了"的时候，
    #   必须能拿到**当时真正发出去的那份 HTML** 才能查。
    #   重新用现在的文章内容渲染一遍是没用的 —— 文章可能已经被改过了。
    content_html = models.TextField("正文 HTML 快照", blank=True, default="")

    title = models.CharField("标题", max_length=200, blank=True, default="")
    image_count = models.PositiveIntegerField("正文图数量", default=0)

    error_code = models.CharField("错误码", max_length=50, blank=True, default="")
    error_message = models.TextField("错误信息", blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "公众号推送"
        verbose_name_plural = "公众号推送"
        ordering = ["-created_at"]
        indexes = [
            # 推送历史按时间倒序查；也用来找"这篇文章最近一次成功的推送"
            models.Index(fields=["article", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"推送 {self.id} ({self.get_status_display()})"
