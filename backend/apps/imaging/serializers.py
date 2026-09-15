"""
配图序列化器。

★ 最重要的一条：**永远不要把 `data` 放进响应里**。

    GeneratedImage.data 是图片二进制，一张几百 KB。列表接口如果把它带上，
    一篇文章 4 张图就是 1~2MB 的 JSON，而且会被序列化成 base64（还要再涨 33%）。
    浏览器会卡、nginx 会慢、用户会以为系统坏了。

    图片本体只从 /api/v1/images/<id>/file 拿，那条路由专门发二进制。
"""

from __future__ import annotations

from rest_framework import serializers

from apps.imaging.models import GeneratedImage


class GeneratedImageSerializer(serializers.ModelSerializer):
    """配图的元数据（不含图片本体）。"""

    # 给了这个，前端 <img src> 直接用就行，不用自己拼路径。
    # 用相对路径：前端和 API 同源部署，相对路径在换域名时不用改任何东西。
    file_url = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = GeneratedImage
        fields = [
            "id",
            "article",
            "suggestion_index",
            "suggestion",
            "prompt",
            "revised_prompt",
            "status",
            "status_display",
            "content_type",
            "width",
            "height",
            "byte_size",
            "caption",
            "anchor_index",
            "sort_order",
            "is_cover",
            "provider",
            "model_name",
            "duration_ms",
            "wechat_url",
            "error_message",
            "file_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj: GeneratedImage) -> str:
        # 没图的时候给空字符串而不是一个会 404 的地址，
        # 前端判空就行，不用先发一个必然失败的请求。
        if not obj.data:
            return ""
        return f"/api/v1/images/{obj.id}/file"


class ImageGenerateSerializer(serializers.Serializer):
    """POST /api/v1/articles/<id>/images 的请求体。"""

    suggestion_index = serializers.IntegerField(
        required=False, min_value=0, max_value=50, default=0
    )
    suggestion = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )
    prompt = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=2000,
        help_text="自定义提示词。留空则用 IMAGE_STYLE_PROMPT + suggestion 自动拼。",
    )
    # ★ 前端必须传它 —— 用户在界面上选了位置就得按用户的来。
    #   留 None 表示"没给"，由服务层用关键词启发式猜一个。
    anchor_index = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0, max_value=50
    )
    caption = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )


class ImageUpdateSerializer(serializers.Serializer):
    """PATCH /api/v1/images/<id> 的请求体。

    所有字段都不是必填 —— PATCH 的语义是"只改我传了的"。
    """

    caption = serializers.CharField(required=False, allow_blank=True, max_length=200)
    anchor_index = serializers.IntegerField(
        required=False, min_value=0, max_value=50
    )
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=999)
    is_cover = serializers.BooleanField(required=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("没有需要修改的字段。")
        return attrs
