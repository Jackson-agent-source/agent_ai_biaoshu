"""热点原始记录、采集状态和用户选题。"""

import uuid

from django.conf import settings
from django.db import models

from apps.trends.categories import default_auto_categories, default_focus_terms


class TrendItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=20, db_index=True)
    external_id = models.CharField(max_length=200)
    title = models.CharField(max_length=500)
    category = models.CharField(max_length=20, default="other", db_index=True)
    research_topic = models.CharField(max_length=40, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="focus_trends")
    url = models.URLField(max_length=1000)
    region = models.CharField(max_length=20, blank=True)
    rank = models.PositiveIntegerField(default=0)
    metric = models.BigIntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["platform", "external_id"], name="uniq_trend_platform_item")]
        indexes = [models.Index(fields=["-last_seen_at"])]


class TrendFetchRun(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "成功"
        SKIPPED = "skipped", "未配置"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=20)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=12, choices=Status.choices)
    item_count = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class TopicCandidate(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "待生成"
        GENERATING = "generating", "生成中"
        GENERATED = "generated", "已生成"
        FAILED = "failed", "生成失败"
        SKIPPED = "skipped", "已跳过"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_candidates")
    cluster_key = models.CharField(max_length=64)
    topic = models.CharField(max_length=500)
    category = models.CharField(max_length=20, default="other", db_index=True)
    angle = models.CharField(max_length=500, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    score = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.READY)
    trends = models.ManyToManyField(TrendItem, related_name="candidates")
    article = models.ForeignKey("articles.Article", null=True, blank=True, on_delete=models.SET_NULL)
    error_message = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "cluster_key"], name="uniq_user_trend_cluster")]
        ordering = ["-score", "-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"])]


class TrendPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trend_preference")
    focus_terms = models.JSONField(default=default_focus_terms)
    auto_categories = models.JSONField(default=default_auto_categories)
    updated_at = models.DateTimeField(auto_now=True)
