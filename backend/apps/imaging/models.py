"""
配图数据模型。

设计要点（为什么图片二进制直接进数据库）：

    GeneratedImage.data 是 BinaryField，图片**不落磁盘**。

    理由：这台服务器上跑的是 Docker Compose，如果图片写文件系统，就得
    新增一个共享卷、给 backend 和 nginx 各挂一次、再加一条 nginx 的
    /media/ 路由，备份也要多管一份。而 mysqldump 本来就会带上二进制字段，
    所以"存数据库"意味着**部署步骤零增加、备份自动覆盖图片**。

    代价如实记录：每张图约 300KB 进库，备份文件会同步变大。
    50 篇文章 × 3 张 ≈ 45MB，这个量级完全没问题。
    真要涨到几个 G，再换文件存储是个局部改动（只动 views 里的取图那一段）。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class GeneratedImage(models.Model):
    """一张 AI 生成的配图。"""

    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已生成"
        FAILED = "failed", "失败"

    # 用 UUID 而不是自增 id：
    #   1. 取图的接口 /api/v1/images/<id>/file 是 AllowAny（浏览器的 <img>
    #      带不了 Authorization 头），UUID4 不可猜，等于一条隐式的访问凭据。
    #   2. 和项目里 Article / AgentRun 的写法一致。
    # 注意：不能用 default=uuid.uuid4 之外的"顺序型"UUID（比如 uuid1），
    # 那样会泄露时间和 MAC 地址，也就能被猜出来。
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    article = models.ForeignKey(
        "articles.Article",
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="所属文章",
    )
    # 冗余一份 user：额度统计要按用户数"今天生成了几张"，
    # 只靠 article__user 也能查，但那样每次都要 join，而且索引不好用。
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generated_images",
        verbose_name="所属用户",
    )

    # ---- 来源：来自文章的第几条配图建议 ----
    suggestion_index = models.PositiveIntegerField("建议序号", default=0)
    suggestion = models.TextField("配图建议原文", blank=True, default="")
    # 实际发给生图模型的提示词（= IMAGE_STYLE_PROMPT + 建议），排查"图不对"时看它
    prompt = models.TextField("实际提示词", blank=True, default="")
    # 有的模型会把你给的提示词改写一遍（OpenAI 的 revised_prompt），存下来对照用
    revised_prompt = models.TextField("模型改写后的提示词", blank=True, default="")

    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # ---- 图片本体 ----
    # null=True 而不是 default=b""：生成中/失败时这一列应该是 NULL，
    # 一眼能看出"还没图"，也能省下空串占位。
    data = models.BinaryField("图片二进制", null=True, blank=True)
    content_type = models.CharField("MIME 类型", max_length=50, blank=True, default="image/jpeg")
    width = models.PositiveIntegerField("宽", null=True, blank=True)
    height = models.PositiveIntegerField("高", null=True, blank=True)
    byte_size = models.PositiveIntegerField("字节数", default=0)

    # ---- 排版信息 ----
    caption = models.CharField("图注", max_length=200, blank=True, default="")
    # 插入位置：0 = 开头（第一个二级标题之前），N = 第 N 个 "## " 小节之后。
    # ★ 这里刻意**不用** Article.outline 的下标 —— outline 是模型给的，
    #   可能和正文里真实出现的 "## " 小标题对不上。
    #   前后端统一按正文里真实的 "## " 行来切段，才不会出现"预览里在图 A 下面、
    #   推到公众号跑到图 B 下面"这种事。
    anchor_index = models.IntegerField("插入位置", default=0)
    # 同一个位置插多张时的先后顺序
    sort_order = models.PositiveIntegerField("排序", default=0)
    is_cover = models.BooleanField("作为封面", default=False)

    # ---- 排查用的元信息 ----
    provider = models.CharField("生图服务", max_length=50, blank=True, default="")
    model_name = models.CharField("模型名", max_length=100, blank=True, default="")
    duration_ms = models.IntegerField("耗时(毫秒)", null=True, blank=True)
    # ★ 正文图上传到微信后拿到的 mmbiz.qpic.cn 地址。
    #   微信不允许正文图片外链，只能用 uploadimg 返回的地址，
    #   所以上传过一次就存下来复用，重复推送时不用再传一遍。
    wechat_url = models.CharField("微信正文图地址", max_length=500, blank=True, default="")
    error_message = models.TextField("错误信息", blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "配图"
        verbose_name_plural = "配图"
        ordering = ["article", "anchor_index", "sort_order", "-created_at"]
        indexes = [
            # 按文章取图 + 按位置排序，是列表接口的主要查询
            models.Index(fields=["article", "anchor_index"]),
            # ★ 这条是**每日额度统计**专用的：
            #   额度直接数数据库里当天的真实行数（硬账本），
            #   清缓存、重启、多 worker 都绕不过去。
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"配图 {self.id} ({self.get_status_display()})"

    @property
    def has_data(self) -> bool:
        """有没有图片二进制。列表接口靠它判断要不要显示"图片没了"。"""
        return bool(self.data)
