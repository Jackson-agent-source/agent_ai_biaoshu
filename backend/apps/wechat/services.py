"""
公众号推送服务。

★ 这里做的是**建草稿**，不是发布。

    个人主体订阅号从 2025-07 起已经拿不到 freepublish/submit 的权限，
    API 层面就发不出去 —— 只能创建草稿，由人**在草稿箱里手动点发布**。
    这正好和产品红线（V1 绝不自动发布）一致，不是我们偷懒。

    所以整个流程是：
        用户点"推送到公众号" → 我们建一份草稿 → 用户在手机公众号后台审核 → 手动发布

★ 本文件里有**两条互不相干**的路，不要把它们合并：

    publish_article_to_wechat()   走微信 API 建草稿，要凭据、要 IP 白名单、
                                  要草稿权限、必须有封面图
    build_article_copy_html()    只拼一份 HTML 给用户**手动复制粘贴**，
                                  **前置条件一个都不要**

    第二条路存在的意义就是"第一条路走不通的时候还有办法发文章"
    （号没有草稿权限、或者用户暂时不想配 AppSecret）。

★★ 复制路径对微信接口做一件**尽力而为**的事（_rehost_images_best_effort）：

    正文图片微信**不允许外链**（见 client.upload_body_image 的说明）。
    实测把本站地址的图粘进公众号编辑器，编辑器不会替我们转存，
    用户看到的是「图片载入失败 / 系统繁忙」——四张图全挂。

    所以复制之前，凡是还没上过微信的配图，都尽力传一次换回 mmbiz 地址。
    **但这一步绝不能变成前置条件**：凭据没配、号没有 uploadimg 权限、
    网络超时，任何一种情况都静默跳过、退回本站地址，功能照常可用。
    凭据没配时**一次微信接口都不调** ——
    test_copy_html_api.py::test_works_without_any_wechat_credentials 守着这条。
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from apps.articles.models import Article
from apps.common.exceptions import WeChatPreconditionError, WeChatPublishError
from apps.common.markdown_sections import section_count
from apps.common.utils import truncate
from apps.imaging import image_utils
from apps.imaging.models import GeneratedImage
from apps.imaging.services import get_article_images, get_cover_image, public_file_url
from apps.wechat.client import WeChatClient, get_wechat_client
from apps.wechat.html_builder import build_wechat_html
from apps.wechat.models import WeChatPublish

logger = logging.getLogger(__name__)

# 微信的硬性长度限制（字符数，中文按 1 个算）
MAX_TITLE_CHARS = 64
MAX_DIGEST_CHARS = 120


def publish_article_to_wechat(
    *,
    article: Article,
    user,
    cover_image_id=None,
    force_new: bool = False,
) -> WeChatPublish:
    """把文章推送到公众号草稿箱。

    失败时抛 WeChatPublishError（中文原因直接给用户看），
    同时把失败记录留在库里。

    cover_image_id：显式指定哪张图当封面。不传就自动挑（见 get_cover_image）。
    force_new：      强制新建草稿，即使之前推过。默认是更新那一份。
    """
    client = get_wechat_client()

    # ---- 1. 检查有没有配图 ----
    # ★ 这一步必须在最前面、而且必须报错，不能"没图就跳过封面"。
    #   微信的 draft/add 要求 thumb_media_id 必填，
    #   没有封面图**根本推不出去**。与其让它到微信那边失败、
    #   返回一个看不懂的错误码，不如在这里就说清楚要做什么。
    images = list(
        get_article_images(article).filter(status=GeneratedImage.Status.SUCCEEDED)
    )
    if not images:
        raise WeChatPreconditionError(
            "这篇文章还没有配图，无法推送。公众号要求草稿必须有封面图，"
            "请先在配图工作台生成至少一张配图。"
        )

    cover = get_cover_image(article, preferred_id=cover_image_id)
    if cover is None:
        raise WeChatPreconditionError("没有找到可用的封面图，请先生成一张配图。")

    record = WeChatPublish.objects.create(
        article=article,
        user=user,
        status=WeChatPublish.Status.RUNNING,
        title=truncate(article.title or "", MAX_TITLE_CHARS, ""),
        image_count=len(images),
    )

    try:
        # ---- 2. 上传正文图 ----
        # ★ 已经传过的（wechat_url 非空）直接复用。
        #   重复推送时这一步会几乎全部命中缓存，推送从几十秒变成一两秒。
        #   注意 wechat_url 是**按图片记录**存的，不是按次推送 ——
        #   只要图片内容没变（重新生成会新建一条记录），地址就一直有效。
        for image in images:
            upload_result = _ensure_body_image(client=client, image=image)
            if not upload_result:
                raise WeChatPublishError(
                    f"第 {image.sort_order + 1} 张配图上传失败，请稍后重试。"
                )

        # ---- 3. 上传封面 ----
        thumb_result = _upload_cover(client=client, image=cover)
        if not thumb_result:
            raise WeChatPublishError("封面图上传失败，请稍后重试。")

        # ---- 4. 拼正文 ----
        content_html = build_wechat_html(markdown=article.content_markdown or "", images=images)
        if not content_html.strip():
            raise WeChatPublishError("文章正文是空的，没有内容可以推送。")

        # ---- 5. 建 / 更新草稿 ----
        payload = {
            "title": truncate(article.title or "未命名文章", MAX_TITLE_CHARS, ""),
            "author": (settings.WECHAT_AUTHOR or "").strip(),
            "digest": truncate(article.summary or "", MAX_DIGEST_CHARS, ""),
            "content": content_html,
            "thumb_media_id": thumb_result,
            # 留空：这是"阅读原文"的链接。我们不自动填 ——
            # 填错会指向一个不相干的页面，比空着更糟。
            "content_source_url": "",
        }

        previous_media_id = "" if force_new else _find_previous_media_id(article)

        if previous_media_id:
            result = client.update_draft(previous_media_id, payload)
            action = WeChatPublish.Action.UPDATE
            media_id = previous_media_id
        else:
            result = client.add_draft(payload)
            action = WeChatPublish.Action.ADD
            media_id = result.media_id

        if not result.ok:
            # ★ 草稿被删了（40007）就退回新建 —— 用户的意图是"推一份草稿过去"，
            #   而不是"必须更新那一份"。因为一份已经不存在的草稿而失败，
            #   对用户来说毫无意义。
            if result.error == "wechat_invalid_media_id":
                logger.warning("草稿 media_id 已失效，改为新建草稿 article_id=%s", article.id)
                result = client.add_draft(payload)
                action = WeChatPublish.Action.ADD
                media_id = result.media_id

        if not result.ok:
            record.status = WeChatPublish.Status.FAILED
            record.action = action
            record.error_code = result.error
            record.error_message = result.message
            record.content_html = content_html
            record.save(
                update_fields=[
                    "status",
                    "action",
                    "error_code",
                    "error_message",
                    "content_html",
                    "updated_at",
                ]
            )
            logger.error(
                "推送公众号失败 article_id=%s error=%s", article.id, result.error
            )
            # 原样把微信给的中文说明抛给用户 —— 40164 白名单、48001 没权限
            # 这类错误都是用户去后台点两下就能解决的，糊成"推送失败"等于让他来问你。
            raise WeChatPublishError(result.message or "推送到公众号失败。")

        record.status = WeChatPublish.Status.SUCCEEDED
        record.action = action
        record.media_id = media_id
        record.content_html = content_html
        record.error_code = ""
        record.error_message = ""
        record.save(
            update_fields=[
                "status",
                "action",
                "media_id",
                "content_html",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

        logger.info(
            "推送公众号成功 article_id=%s action=%s media_id=%s 图片=%s张",
            article.id,
            action,
            media_id,
            len(images),
        )
        return record

    except WeChatPublishError as exc:
        # ★ 这里不能只是 raise。
        #
        #   上面有**四条**抛 WeChatPublishError 的路径：
        #       · 正文图上传失败（上面第 2 步）      -> 没有落库
        #       · 封面图上传失败（上面第 3 步）      -> 没有落库
        #       · 正文为空（上面第 4 步）            -> 没有落库
        #       · 微信返回了错误码（第 5 步）        -> **已经**落库成 FAILED
        #   只有最后一条自己写了记录。如果这里直接往上抛，
        #   钱三种情况会让记录永远停在 RUNNING：
        #
        #       · 前端"上次推送"那一行会一直显示「推送中」，永远不会变成失败
        #         —— 用户以为它还在跑，其实早就在等他了
        #       · 而且这条 RUNNING 会一直是"最近一次推送"，
        #         把后面真正的成功记录挤在它下面
        #
        #   所以这里按"还在 RUNNING 就补一条失败记录"来处理，
        #   已经落过库的那条不会被覆盖（它的 error_code 是微信的机器码，更有价值）。
        #
        #   注：正文图上传失败的具体微信 errcode（例如 40009）只在上面那行
        #   logger.error 里，没往上传 —— 传它要改 _ensure_body_image 的返回类型，
        #   而 error_message 已经说了"第几张图上传失败"，够用户和排查用了。
        if record.status == WeChatPublish.Status.RUNNING:
            record.status = WeChatPublish.Status.FAILED
            record.error_code = getattr(exc, "code", "") or "WECHAT_PUBLISH_ERROR"
            record.error_message = exc.message
            record.save(
                update_fields=["status", "error_code", "error_message", "updated_at"]
            )
        raise
    except Exception as exc:
        # 任何没预料到的异常也要留痕，否则用户只会看到"服务器内部错误"，
        # 而库里什么记录都没有，完全无从查起。
        logger.exception("推送公众号出现未预期异常 article_id=%s", article.id)
        record.status = WeChatPublish.Status.FAILED
        record.error_code = "unexpected_error"
        record.error_message = str(exc)[:500]
        record.save(update_fields=["status", "error_code", "error_message", "updated_at"])
        raise WeChatPublishError("推送过程中出现意外错误，请查看服务器日志。") from exc


# ---------------------------------------------------------------------------
# 内部步骤
# ---------------------------------------------------------------------------
def _ensure_body_image(*, client: WeChatClient, image: GeneratedImage) -> bool:
    """确保这张图已经上传到微信，返回是否成功。

    已经有 wechat_url 的直接跳过（重复推送时省掉整个上传）。
    """
    if image.wechat_url:
        return True

    # 从文件系统读（原来是从 BinaryField 取）。read_bytes() 读不到时返回 b""，
    # 所以下面那句判空同时覆盖"记录里没有文件"和"文件在磁盘上丢了"两种情况。
    data = image.read_bytes()
    if not data:
        logger.error("配图没有图片数据，无法上传 image_id=%s", image.id)
        return False

    # ★ 必须压到 1MB 以内，否则微信直接拒收（40009）。
    #   生图模型给的是 1.5~3MB 的图，这一步不是优化，是必需环节。
    compressed, content_type = image_utils.normalize_for_wechat_body(data)
    if not compressed:
        logger.error("配图压缩后为空 image_id=%s", image.id)
        return False

    extension = "png" if content_type == "image/png" else "jpg"
    result = client.upload_body_image(compressed, filename=f"{image.id}.{extension}")
    if not result.ok:
        logger.error("上传正文图失败 image_id=%s error=%s", image.id, result.error)
        return False

    image.wechat_url = result.url
    image.save(update_fields=["wechat_url", "updated_at"])
    return True


def _upload_cover(*, client: WeChatClient, image: GeneratedImage) -> str:
    """上传封面，返回 thumb_media_id；失败返回空字符串。"""
    data = image.read_bytes()
    if not data:
        logger.error("封面图没有图片数据 image_id=%s", image.id)
        return ""

    # ★ 封面必须单独生成：只收 JPG、约 64KB、而且是 900x500 的横图。
    #   直接拿正文图当封面，微信会自己裁中间一块，经常把主体裁掉。
    thumb = image_utils.make_wechat_thumb(data)
    if not thumb:
        logger.error("封面图生成失败 image_id=%s", image.id)
        return ""

    result = client.upload_thumb(thumb, filename=f"{image.id}-cover.jpg")
    if not result.ok:
        logger.error("上传封面失败 image_id=%s error=%s", image.id, result.error)
        return ""

    return result.media_id


def _find_previous_media_id(article: Article) -> str:
    """找这篇文章最近一次**成功**推送的 media_id。

    有的话就走 draft/update，草稿箱里始终只有一份 ——
    用户"改一版推一次"不会攒出一堆重复草稿。
    """
    previous = (
        WeChatPublish.objects.filter(
            article=article,
            status=WeChatPublish.Status.SUCCEEDED,
        )
        .exclude(media_id="")
        .order_by("-created_at")
        .first()
    )
    return previous.media_id if previous else ""


def get_publish_history(article: Article):
    """推送历史，最新的在前。"""
    return WeChatPublish.objects.filter(article=article).order_by("-created_at")


# ---------------------------------------------------------------------------
# 复制到公众号（不走微信 API 的那条路）
# ---------------------------------------------------------------------------
def _copy_image_url(image: GeneratedImage) -> str:
    """复制路径的取图地址。

    ★ 优先用已经上传到微信的 mmbiz 地址（如果有的话）：它在 https 的
      mmbiz.qpic.cn 上，粘进公众号编辑器必定显示得出来。
      从没推过（公众号没有草稿权限、或者用户根本没配 AppSecret）才退回本站地址 ——
      那条路能不能显示取决于公众号编辑器会不会替我们把外链图重新上传，
      而 mmbiz 那条不用赌。

    ★ 所以响应里会**混着两种域名**，这是故意的，不要"统一"成一种。
    """
    return (getattr(image, "wechat_url", "") or "") or public_file_url(image)


# ★ 连续失败这么多张就放弃剩下的。用来兜住"整个号没有权限"或"网络不通"这类
#   系统性故障 —— 不然每张图都要等一次超时（默认 60 秒），点一次「复制」
#   能卡好几分钟。单张图坏掉不会连败两次，所以不会误伤。
_REHOST_MAX_CONSECUTIVE_FAILURES = 2


def _wechat_credentials_present() -> bool:
    """凭据配全了没有。

    ★ 故意直接读 settings，**不走 get_wechat_client()** ——
      后者在没配时会抛 WeChatNotConfiguredError，而这里只想安静地判断一下。
      更重要的是：凭据没配时调用方一次微信接口都不会碰，
      这正是 test_works_without_any_wechat_credentials 守的那条线。
    """
    app_id = (getattr(settings, "WECHAT_APP_ID", "") or "").strip()
    app_secret = (getattr(settings, "WECHAT_APP_SECRET", "") or "").strip()
    return bool(app_id and app_secret)


def _rehost_images_best_effort(images: list[GeneratedImage]) -> int:
    """把还没上过微信的配图尽力传一次，返回成功张数。

    ★ 为什么值得做：正文图片微信**不允许外链**，公众号编辑器也不会替我们
      把外链图转存 —— 实测四张图全报「图片载入失败」。上传一次换回
      mmbiz.qpic.cn 的地址，那个必定显示得出来。

    ★ 为什么是"尽力而为"：这条路必须保住"什么前置条件都没有"。
      所以**任何一步失败都只是少一次优化** —— 记一条日志、退回本站地址，
      不抛异常、不改变返回结果、不让接口变 4xx/5xx。

    ★ 失败是静默的，但**不是无声的**：上传失败会打 WARNING 日志，
      排查"图为什么还是裂的"时看 backend 日志即可。
      想主动检查配置能不能用，跑 `python manage.py wechat_check`。
    """
    if not _wechat_credentials_present():
        # ★ 常见情况，而且完全正常：用户没打算用公众号 API。
        #   这里连日志都不打，免得每次点「复制」都刷一行。
        return 0

    pending = [img for img in images if not (getattr(img, "wechat_url", "") or "")]
    if not pending:
        return 0

    try:
        client = get_wechat_client()
    except Exception:  # noqa: BLE001 —— 这条路什么错都不能往外抛
        logger.warning("复制正文前拿微信客户端失败，配图沿用本站地址", exc_info=True)
        return 0

    # ★ 先单独把 token 拿一次。IP 白名单没加（40164）、AppSecret 写错（40125）
    #   这类问题在这一步就暴露了，此时直接放弃，不用一张张图去撞。
    token = client.get_access_token()
    if not token.ok:
        logger.warning(
            "拿 access_token 失败（%s/%s），%d 张配图沿用本站地址",
            token.error,
            token.errcode,
            len(pending),
        )
        return 0

    uploaded = 0
    consecutive_failures = 0
    for image in pending:
        try:
            ok = _ensure_body_image(client=client, image=image)
        except Exception:  # noqa: BLE001
            logger.warning("上传配图抛异常 image_id=%s", image.id, exc_info=True)
            ok = False

        if ok:
            uploaded += 1
            consecutive_failures = 0
            continue

        consecutive_failures += 1
        if consecutive_failures >= _REHOST_MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "配图连续 %d 张上传失败，剩下的 %d 张本次不再尝试"
                "（多半是这个号没有 uploadimg 权限，或微信不通）",
                consecutive_failures,
                len(pending) - uploaded - consecutive_failures,
            )
            break

    if uploaded:
        logger.info("复制正文前上传了 %d/%d 张配图，正文将用 mmbiz 地址", uploaded, len(pending))
    return uploaded


def build_article_copy_html(*, article: Article) -> dict:
    """拼一份"复制到公众号"用的正文 HTML。

    ★ 不含标题/作者/摘要 —— 公众号编辑器把它们当三个独立的输入框，
      塞进正文里反而要用户手动删掉。

    ★ 和推送路径（publish_article_to_wechat）的三点不同，都是刻意的：

        1. **不要求有配图**。推送必须有封面图（微信 draft/add 的硬要求），
           而复制只复制文字是完全正当的用法。
        2. **前置条件一个都没有**。号没有草稿权限（48001）照样能用这个功能 ——
           这正是它存在的意义，tests 里有一条专门守着这一点。
           唯一会碰微信接口的是 _rehost_images_best_effort()，
           而它**没配凭据就完全不调用**，失败也只是退回本站地址。
        3. 图片来源优先 mmbiz 地址，没有就用本站地址（见 _copy_image_url）。

    返回 {"html", "image_count", "section_count", "warning"}。
    """
    succeeded = get_article_images(article).filter(
        status=GeneratedImage.Status.SUCCEEDED
    )
    # ★ 有图片文件才算数。image_file 为空的记录进 build_wechat_html 也只会被丢掉。
    with_file = list(succeeded.exclude(image_file=""))  # FileField 空值是空串不是 NULL
    no_file = succeeded.count() - len(with_file)

    # ★ 必须放在下面算 images **之前**：上传成功会把 wechat_url 写回对象，
    #   紧接着的 _copy_image_url 就取到 mmbiz 地址了。
    #   这一步失败不影响任何东西 —— 详见 _rehost_images_best_effort 的说明。
    _rehost_images_best_effort(with_file)

    # ★ 先在这里把地址算出来，取不到地址的图**提前摘掉**，而不是留给
    #   build_wechat_html 内部跳过。否则 image_count 会按"候选张数"报，
    #   而 html 里其实一张图都没有 —— 前端会告诉用户"已复制正文和 3 张配图"，
    #   用户粘过去发现没图。image_count 必须等于**真的拼进去了几张**。
    images = [image for image in with_file if _copy_image_url(image)]
    no_url = len(with_file) - len(images)

    html = build_wechat_html(
        markdown=article.content_markdown or "",
        images=images,
        src_getter=_copy_image_url,
    )

    warnings: list[str] = []
    if no_file > 0:
        warnings.append(f"有 {no_file} 张配图没有图片文件，没有复制进去。")

    # ★ 只有真有图被丢下的时候才提地址的事 —— 纯文字复制根本不需要图片地址，
    #   没配 MEDIA_PUBLIC_BASE_URL 也完全正常，不该给用户报一个假的警告。
    if no_url > 0:
        base = (getattr(settings, "MEDIA_PUBLIC_BASE_URL", "") or "").strip()
        if not base:
            warnings.append(
                f"有 {no_url} 张配图没有复制进去：服务器还没有配置图片的对外地址。"
                "请在服务器的 .env 里加一行 MEDIA_PUBLIC_BASE_URL=https://<你的域名>"
                "（★ 要和网站本身同源、带 https:// 前缀、不带端口号），"
                "改完执行 docker compose up -d backend。"
            )

    return {
        "html": html,
        "image_count": len(images),
        # ★ 这里必须复用 section_count()，不要自己数 "## " 的个数：
        #   它等于 len(split_markdown_sections()) - 1，也就是 build_wechat_html
        #   内部用的那个 last_index —— 同一个函数才能保证和配图接口的口径一致。
        "section_count": section_count(article.content_markdown or ""),
        "warning": "".join(warnings),
    }
