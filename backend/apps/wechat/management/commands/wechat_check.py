"""检查公众号配置到底能不能用。

    docker compose exec backend python manage.py wechat_check

★ 为什么要有这个命令：公众号出问题时，"哪一步不通"和"怎么修"的对应关系
  完全不明显。IP 白名单没配报 40164、AppSecret 写错报 40125、个人订阅号
  没有接口权限报 48001 —— 而这三个原因对应三件完全不同的补救动作。
  在没有这个命令之前，唯一的办法是真的去推一篇文章看它炸在哪。

★★ 这个命令**绝不打印任何密钥**，一个字符都不打：

    · WECHAT_APP_SECRET 只报长度（32 位才可能是对的）
    · access_token 连长度都不报 —— 它等同于密码，拿到就能以你的名义调接口
    · 上传成功后只报地址的**主机名**（mmbiz.qpic.cn），不报完整 URL

   别改成"报一下前几位方便核对" —— 这个命令的输出会进日志、进聊天窗口、
   进截图，而密钥只泄漏一次就等于永久泄漏（公众号的 AppSecret 一旦泄漏，
   只能重置，重置会让所有正在用的地方同时失效）。
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common.exceptions import WeChatNotConfiguredError
from apps.imaging.models import GeneratedImage
from apps.wechat.client import get_wechat_client
from apps.wechat.services import _ensure_body_image


def credential_state(name: str, value: str) -> str:
    """把配置项描述成一句能核对、又不泄漏值的话。"""
    v = (value or "").strip()
    if not v:
        return "未配置"
    return f"已配置（长度 {len(v)}）"


def host_of(url: str) -> str:
    """只取主机名。完整地址没必要出现在输出里。"""
    try:
        return urlparse(url or "").hostname or "（拿不到主机名）"
    except ValueError:
        return "（地址解析失败）"


class Command(BaseCommand):
    help = "检查公众号配置：凭据 / access_token / 正文图上传权限（不打印任何密钥）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--image-id",
            default="",
            help="指定拿哪张配图试上传；不填就自动挑一张还没传过微信的",
        )
        parser.add_argument(
            "--no-upload",
            action="store_true",
            help="只查凭据和 access_token，不真的上传（不想改动任何数据时用）",
        )

    def handle(self, *args, **options):
        app_id = getattr(settings, "WECHAT_APP_ID", "") or ""
        app_secret = getattr(settings, "WECHAT_APP_SECRET", "") or ""

        self.stdout.write("")
        self.stdout.write("==== 1. 凭据 ====")
        self.stdout.write(f"  WECHAT_APP_ID     : {credential_state('WECHAT_APP_ID', app_id)}")
        self.stdout.write(f"  WECHAT_APP_SECRET : {credential_state('WECHAT_APP_SECRET', app_secret)}")
        self.stdout.write(f"  超时设置           : {getattr(settings, 'WECHAT_TIMEOUT', '?')} 秒")

        if not app_id.strip() or not app_secret.strip():
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("凭据没配全 —— 后面两步没法测。"))
            self.stdout.write(
                "  去 公众号后台 → 设置与开发 → 基本配置 → 公众号开发信息\n"
                "  拿 AppID 和 AppSecret，填进服务器根目录的 .env，然后：\n"
                "      docker compose up -d backend      ← 必须是 up -d，restart 不重读 .env"
            )
            return

        # ---- 2. access_token ----
        # ★ 拿不到 token 就没必要往下走：上传图也一定要 token。
        self.stdout.write("")
        self.stdout.write("==== 2. 能不能拿到 access_token ====")
        try:
            client = get_wechat_client()
        except WeChatNotConfiguredError as exc:
            self.stdout.write(self.style.ERROR(f"  配置不完整：{exc}"))
            return

        token = client.get_access_token()
        if not token.ok:
            self.stdout.write(self.style.ERROR(f"  失败  errcode={token.errcode}  {token.error}"))
            self.stdout.write(f"  {token.message}")
            self.stdout.write("")
            self.stdout.write("  ↑ 40164 就是 IP 白名单没加；40125/40013 是密钥或 AppID 填错了。")
            return

        # ★ 不打印 token 本身，连长度都不打。
        self.stdout.write(self.style.SUCCESS("  成功（token 已拿到，不打印）"))

        if options["no_upload"]:
            self.stdout.write("")
            self.stdout.write("  加了 --no-upload，跳过上传测试。")
            return

        # ---- 3. 正文图上传权限 ----
        self.stdout.write("")
        self.stdout.write("==== 3. 能不能调 uploadimg（正文图上传）====")
        image = self._pick_image(options["image_id"])
        if image is None:
            self.stdout.write("  库里没有带图片文件的配图，没法试。")
            self.stdout.write("  先去生成一张配图，或者用 --image-id 指定一张。")
            return

        self.stdout.write(f"  拿这张试：image_id={image.id}  当前 wechat_url={'有' if image.wechat_url else '无'}")

        # ★ 已经有地址的不重复传，否则测出来的是"缓存命中"，证明不了权限。
        if image.wechat_url:
            self.stdout.write("  （这张已经传过了，不能用来测权限。换一张没传过的重试。）")
            return

        try:
            ok = _ensure_body_image(client=client, image=image)
        except Exception as exc:  # noqa: BLE001 —— 这条命令是拿来排错的，什么错都要报出来
            self.stdout.write(self.style.ERROR(f"  抛异常了：{type(exc).__name__}: {exc}"))
            return

        if not ok:
            self.stdout.write(self.style.ERROR("  失败"))
            self.stdout.write(
                "  ↑ 看上面 backend 日志里那行 '上传正文图失败' 的 error= 是什么。\n"
                "    如果是 48001（wechat_api_unauthorized），说明这个号没有 uploadimg 权限 ——\n"
                "    那就走不通自动上传，复制到公众号之后**手动插入实拍图/配图**即可。"
            )
            return

        image.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"  成功  地址主机 = {host_of(image.wechat_url)}"))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("结论：这个号可以自动上传正文图。"))
        self.stdout.write("  「复制到公众号」会把没传过的配图自动传一次，正文里用 mmbiz 地址，")
        self.stdout.write("  粘进编辑器必定显示 —— 不会再出现「图片载入失败」。")

    def _pick_image(self, image_id: str):
        """挑一张可以用来试上传的图。

        优先挑**还没传过微信**的 —— 已经传过的会因为缓存直接返回成功，
        测不出接口权限到底有没有。
        """
        base = GeneratedImage.objects.filter(
            status=GeneratedImage.Status.SUCCEEDED
        ).exclude(image_file="")

        if image_id:
            return base.filter(id=image_id).first()

        return base.filter(wechat_url="").order_by("-created_at").first() or base.order_by(
            "-created_at"
        ).first()
