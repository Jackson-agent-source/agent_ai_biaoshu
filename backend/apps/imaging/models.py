"""
配图数据模型。

设计要点（图片存文件系统，不存数据库）：

    GeneratedImage.image_file 是 FileField，图片落在 MEDIA_ROOT 下，
    由 Docker 命名卷 uploads 持久化，生产环境由 nginx 直接从卷里发出去。

    ★ 这里改过一次主意，把两次的理由都记下来，免得后人反复推翻：

    最初是 BinaryField（图片二进制进 MySQL），理由是"mysqldump 本来就会带上
    二进制字段，存数据库等于部署步骤零增加、备份自动覆盖图片"。

    换成文件系统的原因有三个，第一个是决定性的：
      1. 每张图约 300KB 直接进库，MySQL 的 innodb_log_file_size 和
         max_allowed_packet 都要跟着图片大小走；而 mysqldump 出来的备份里
         混着几百 MB 的二进制，restore 一次要很久，也没法只恢复图片。
      2. 图片经 Django 进程转发（旧 ImageFileView）意味着每张图都占一个
         gunicorn 的 gthread worker（只有 3×4=12 个），图片一多就把
         API 请求饿死了。交给 nginx 发是零成本的。
      3. 浏览器能直接缓存、CDN 能直接接管，Django 侧不用管 Range/ETag。

    代价如实记录（都会在 README 的部署章节里写到）：
      · 多了一个 uploads 命名卷，`docker compose down -v` 会**连图片一起删**
      · 备份流程要同时打包数据库和这个卷，只 dump 数据库会丢图
      · 删记录不会再自动删文件，得靠下面的 post_delete 信号兜住

★ 关于取图路径的安全性：

    文件名里是一段**独立的 uuid4**（不是记录 id），由 image_upload_to 生成。
    这不是"顺手加的随机数"，而是这条链路上唯一的访问控制 ——
    <img src> 带不了 Authorization 头，取图接口只能是公开的，
    所以"路径猜不出来"就是全部的安全性。
    刻意不把记录的 id 编进路径：那样一旦有人为了 admin 方便把主键换成
    自增或时间有序的 UUID，整个 media 目录就同时变成可枚举的，
    而且不会有任何报错、也不会有任何测试变红。
    扩展名走白名单：nginx 从**同源**发 /media/，绝不能让 .svg/.html
    这类能带脚本的文件落进那个目录。
"""

from __future__ import annotations

import logging
import os
import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)

# 允许落盘的扩展名白名单。
# ★ 这是一条**安全**约束，不是格式偏好：/media/ 和网站同源，
#   一个 .svg 或 .html 落进去就是一个存储型 XSS 的入口。
ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


def image_upload_to(instance: "GeneratedImage", filename: str) -> str:
    """决定图片落在 MEDIA_ROOT 下的哪个位置。

    形如 images/2026/09/3f2a...c1.jpg（32 位 uuid4 十六进制）。

    ★ 为什么必须是 callable，而不是 upload_to="images/%Y/%m/" 这种字符串：
      字符串写法下 Django 会用"传进来的文件名 + 冲突时的随机后缀"，
      路径的不可猜性就绑死在记录 id 上了（见模块开头的说明）。
      这里用一条**独立于主键**的 uuid4 把这个属性焊死。

    ★ 顺带的好处：重新生成必然是一个新文件名，永远不覆盖旧文件，
      所以 nginx 那边可以放心给 /media/ 打 immutable 的长缓存。

    ★ 分片用 timezone.now() 而**不是** instance.created_at：
      created_at 是 auto_now_add，在实例保存前是 None，
      在 upload_to 里读它会直接 TypeError: unsupported format string
      passed to NoneType。这是个很容易踩的坑。
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = ".jpg"
    return f"images/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class GeneratedImage(models.Model):
    """一张 AI 生成的配图。"""

    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已生成"
        FAILED = "failed", "失败"

    # 用 UUID 而不是自增 id：
    #   1. 取图接口 /api/v1/images/<id>/file 是 AllowAny（浏览器的 <img>
    #      带不了 Authorization 头），UUID4 不可猜，等于一条隐式的访问凭据。
    #      （正常取图走 nginx 的 /media/，那条路靠的是文件名里的 uuid4，
    #        见 image_upload_to 的说明。）
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
    # ★ 用 FileField 而不是 ImageField：
    #   ImageField 的价值是"校验用户上传的确实是图片"，而我们的图是
    #   providers.py 从生图 API 拿到的 bytes，本来就不是用户上传的。
    #   而且它的 width_field/height_field 会把宽高写库，可下面的
    #   width/height 是 image_utils.probe() 算的、还允许是 None，
    #   两套逻辑叠在一起只会打架。
    #
    # ★ 用 default="" 而不是 null=True：
    #   FileField 的空值是**空字符串**不是 NULL，官方明确不建议对它用
    #   null=True（会出现 "" 和 NULL 两种"空"，查询要写两遍）。
    image_file = models.FileField(
        "图片文件",
        upload_to=image_upload_to,
        max_length=255,
        blank=True,
        default="",
    )
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
        """有没有图片文件。界面靠它判断要不要显示"图片没了"。

        FileField 的 __bool__ 返回 bool(self.name)，空串就是 False，
        所以这里不需要额外判空。
        """
        return bool(self.image_file)

    def read_bytes(self) -> bytes:
        """把图片读成 bytes；读不到返回 b""。

        ★ 不抛异常，和 image_utils 里那些函数一个态度：
          文件被手工删掉了、卷没挂上、磁盘满了，都只是"这张图没了"，
          不该让整个推送流程抛 500。上层把空 bytes 变成一句中文提示。

        ★ 全项目只有微信上传那两处需要 bytes（微信的 uploadimg /
          upload_thumb 收的就是字节流），所以读盘这件事收敛在这一个方法里。
        """
        if not self.image_file:
            return b""
        try:
            with self.image_file.open("rb") as fh:
                return fh.read()
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                "配图文件读取失败 image_id=%s name=%s: %s",
                self.id,
                self.image_file.name,
                exc,
            )
            return b""


@receiver(post_delete, sender=GeneratedImage)
def _delete_image_file(sender, instance: GeneratedImage, **kwargs) -> None:
    """删记录时把磁盘上的文件一起删掉。

    ★ FileField 的经典坑：**删模型不会删文件**。
      不管的话，media/ 目录会随着"重新生成 → 删掉不满意的"无限膨胀，
      而数据库里一条记录都没有，谁也不知道那些文件还有没有用。

    ★ 为什么用信号而不是在 services.delete_image() 里手写一句：
      Article 被删除时会 CASCADE 删掉它的 GeneratedImage，
      那条路径根本不经过 services（admin 里删文章走的就是它）。
      信号对"显式删除"和"级联删除"一视同仁。

    ★ 重复删除是安全的：FileSystemStorage.delete() 自己吞掉 FileNotFoundError。

    ★ 放在 models.py 底部而不是单独的 signals.py + apps.ready()：
      models.py 必然会被 import，不可能漏加载。少一个启动时的失败面。
    """
    if instance.image_file:
        instance.image_file.delete(save=False)
