"""
配图序列化器。

★ 最重要的一条：**永远不要把 `image_file` 放进 Meta.fields**。
  只暴露下面这个派生的 file_url。

    理由是两条，比"省流量"更硬：
      1. image_file 会序列化成存储层的 name（images/2026/09/xxx.jpg），
         等于把磁盘布局告诉前端，将来换存储后端就是一个破坏性变更。
      2. 前端要的只是"一个能塞进 <img src> 的地址"，那就只给它地址。

    图片本体走 /media/（生产由 nginx 从共享卷直接发，不进 Django）。
"""

from __future__ import annotations

from rest_framework import serializers

from apps.imaging.models import GeneratedImage


class GeneratedImageSerializer(serializers.ModelSerializer):
    """配图的元数据（不含图片本体）。"""

    # 给了这个，前端 <img src> 直接用就行，不用自己拼路径。
    # 用相对路径 /media/...：dev 由 vite proxy 转给后端（config/urls.py 里
    # DEBUG 下的 static() 兜底），prod 由 nginx 直发 ——
    # 两端拿到的是同一个字符串，所以这里不做任何环境判断，
    # 也**不要**改写成 request.build_absolute_uri()（那会把 Host 写死进响应）。
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
        if not obj.image_file:
            return ""
        # image_file.url == MEDIA_URL + name，例如 /media/images/2026/09/xxx.jpg
        return obj.image_file.url


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
