"""
配图服务层。

★ 照项目约定：View 不直接写数据库，所有写操作都从这里走。
  这样"额度检查 → 调外部服务 → 存图"这个顺序只有一个地方实现，
  不会出现某个入口漏掉额度检查的情况。
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article
from apps.common.exceptions import (
    ImageLimitExceededError,
    ImageProviderError,
    ImageQuotaExceededError,
    ImageUploadInvalidError,
)
from apps.common.markdown_sections import section_titles
from apps.common.utils import truncate
from apps.imaging import image_utils
from apps.imaging.models import GeneratedImage
from apps.imaging.providers import get_image_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def get_article_images(article: Article):
    """取一篇文章的全部配图，按"插入位置 → 顺序 → 创建时间"排好。"""
    return article.images.all().order_by("anchor_index", "sort_order", "created_at")


def public_file_url(image: GeneratedImage) -> str:
    """配图的**对外绝对**地址；没配基地址或没有文件时返回空串。

    给"复制到公众号"这类**要在别人的页面上渲染**的场合用。

    ★ 为什么这里能给绝对地址，而 GeneratedImageSerializer.file_url 明确不许：
      那个字段喂的是**本站页面**的 <img src>，相对路径在 dev（vite proxy 转给后端）
      和 prod（nginx 直发）两端拿到的是同一个字符串，所以不该把 Host 写死进响应。
      但复制出去的 HTML 会被粘到 mp.weixin.qq.com 上 —— 相对路径在那里会被解析成
      mp.weixin.qq.com/media/... ，图必裂。两边的要求不同，不是自相矛盾。

    ★★ 绝对不要用 request.build_absolute_uri() 来兜底。frontend/nginx.conf 用的是
       `proxy_set_header Host $host`，而 nginx 的 $host 由请求头决定、且**不带端口**，
       于是它会给出 http://<IP>/media/... 这种地址 —— 漏了端口、协议还可能不对。
       而它的症状（粘过去图看不见）和"https 页面里的 http 图片被浏览器拦掉"
       一模一样，极难分辨。所以基地址只从配置读，不从请求里猜。

    ★ 没配 MEDIA_PUBLIC_BASE_URL 时返回空串而不是相对路径：
      返回相对路径的话，用户粘过去会看到一排裂图；返回空串则由调用方
      （build_article_copy_html）跳过这些图并明确提示用户去配。
      宁可少图，也不要一墙裂图。
    """
    if not image.image_file:
        # FileField 的空值是空串不是 NULL。
        # ★ 这里不能读 .url —— 文件为空时 Django 会抛 ValueError。
        return ""

    base = (getattr(settings, "MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""

    # image_file.url 是相对路径 /media/images/2026/09/xxx.jpg
    return f"{base}{image.image_file.url}"


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------
def enforce_image_quota(*, user) -> None:
    """配图每日额度检查。0 = 不限制。

    和 AgentService._enforce_daily_quota 完全同一套思路（**硬账本**）：
        直接数 GeneratedImage 表里当天的真实行数。
        清缓存、重启容器、换 worker、改 CACHES_BACKEND 都绕不过去。
        也不需要新建额度表 —— 表里已经有"谁、什么时候"了。

    ★ "今天零点"必须按**上海时区**算。created_at 存的是 UTC（USE_TZ=True），
      直接拿 UTC 零点当分界线的话，额度会在北京时间早上 8 点重置 ——
      这种 bug 平时看不出来，只在有人抱怨"我额度半夜就没了"时才暴露。

    ★★ 统计时**排除用户自己上传的实拍图**（source=UPLOAD）。

      这条额度是防"生图 API 烧钱"的，上传自己的照片不花一分钱。
      不排除的话，用户传了 8 张实拍图就会看到"你今天已生成 8 张配图，
      达到每日上限" —— 而他一张都没生成过，完全不知道该做什么
      （详见 GeneratedImage.Source 的说明）。

      ★ 注意两个计数要**用同一个过滤条件**（下面抽成了 _counted_today），
        否则单用户额度和全站额度的口径会不一致，
        而且这种不一致只在两个额度都开着的部署上才显形。
    """
    per_user = settings.IMAGE_DAILY_QUOTA_PER_USER
    global_cap = settings.IMAGE_DAILY_QUOTA_GLOBAL
    if per_user <= 0 and global_cap <= 0:
        return

    day_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

    def _counted_today(**extra):
        """今天**算额度**的配图。上传的实拍图不算。"""
        return GeneratedImage.objects.filter(
            created_at__gte=day_start, **extra
        ).exclude(source=GeneratedImage.Source.UPLOAD)

    if per_user > 0:
        used = _counted_today(user=user).count()
        if used >= per_user:
            logger.warning(
                "用户达到每日配图额度 user_id=%s used=%s limit=%s",
                getattr(user, "id", None),
                used,
                per_user,
            )
            raise ImageQuotaExceededError(
                f"你今天已经生成了 {used} 张配图，达到每日上限（{per_user} 张）。"
                f"请明天再试，或联系管理员调整额度。"
            )

    if global_cap > 0:
        total = _counted_today().count()
        if total >= global_cap:
            logger.warning("全站每日配图总量已达上限 total=%s limit=%s", total, global_cap)
            raise ImageQuotaExceededError("今天全站的配图额度已经用完，请明天再试。")


def enforce_article_limit(*, article: Article) -> None:
    """单篇文章的配图数量上限。

    这条不是防烧钱（那是每日额度的事），是防止一篇文章被塞进几十张图 ——
    公众号正文图太多会很难读，而且推送的请求体也会变得很大。
    """
    limit = settings.IMAGE_MAX_PER_ARTICLE
    if limit <= 0:
        return
    current = article.images.count()
    if current >= limit:
        raise ImageLimitExceededError(
            f"这篇文章已经有 {current} 张配图，达到单篇上限（{limit} 张）。"
            f"请先删掉不想要的，或者调大 IMAGE_MAX_PER_ARTICLE。"
        )


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------
def build_prompt(suggestion: str) -> str:
    """把"配图建议"拼成真正发给生图模型的提示词。

    前半段是用户在 .env 里配置的画风（IMAGE_STYLE_PROMPT），
    后半段是模型自己写的画面描述。

    ★ 风格提示词里明确要求"不要出现文字"是有原因的：
      生图模型画的字几乎都是错的（尤其中文），而公众号配图里出现
      一串乱码似的字母，比没有图还难看。
    """
    style = (settings.IMAGE_STYLE_PROMPT or "").strip()
    body = (suggestion or "").strip()
    if style:
        return f"{style}\n\n画面内容：{body}"
    return body


# ---------------------------------------------------------------------------
# 插入位置：关键词启发式猜一个默认值
# ---------------------------------------------------------------------------
def guess_anchor_index(*, article: Article, suggestion: str, index: int = 0) -> int:
    """根据配图建议的文字，猜一个合理的插入位置。

    这只是**默认值**，界面上有下拉框让用户改，所以宁可猜得保守一点：
    猜不中就是回到 0（开头），用户一眼能看出来并且一秒钟就能改。

    规则（按优先级）：
        1. 建议词里出现"开头/封面/引入/导语/题图" → 0
        2. 建议词里**包含某个二级小标题的文字** → 那个小节的位置
        3. 都没有 → 按建议的序号大致均分到各小节
    """
    text = (suggestion or "").strip()
    titles = section_titles(article.content_markdown or "")

    if not titles:
        return 0

    if any(kw in text for kw in ("开头", "封面", "题图", "引入", "导语", "首图")):
        return 0

    # 最长匹配优先：先试完整标题，再试标题的前 6 个字。
    # 建议词往往是"关于「xxx」这一节配一张…"这种写法，
    # 完整标题就是最可靠的信号。
    for position, title in enumerate(titles, start=1):
        if title and title in text:
            return position

    # 退一步：用标题的前几个字去撞（AI 复述标题时常常会省略后半截）
    for position, title in enumerate(titles, start=1):
        head = title[:6]
        if len(head) >= 3 and head in text:
            return position

    # 完全没线索：按序号均分。第 0 张放开头，之后依次往后铺，
    # 比"全堆在开头"或者"全堆在最后"都更像样。
    return min(index % (len(titles) + 1), len(titles))


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def generate_image(
    *,
    article: Article,
    user,
    suggestion: str = "",
    suggestion_index: int = 0,
    prompt: str = "",
    anchor_index: int | None = None,
    caption: str = "",
) -> GeneratedImage:
    """生成一张配图并保存。

    ★ 一次请求**只生成一张**。原因：
        · 一张要 30~90 秒，几张串起来就超过 nginx 的 600 秒超时了
        · 一张失败不影响已经生成好的其他张
        · 用户可以只重做不满意的那一张
        · 不需要引入 Celery（项目里没有，也不该为这个引进来）

    失败时抛 ImageProviderError（中文原因直接给用户看），
    同时把失败记录留在库里 —— 排查"为什么这张总失败"时有据可查。
    """
    # 额度先查。放在最前面，避免"调完 API 才发现超额度"这种既花钱又报错的情况。
    enforce_image_quota(user=user)
    enforce_article_limit(article=article)

    final_prompt = (prompt or "").strip() or build_prompt(suggestion)
    if not final_prompt:
        raise ImageProviderError("配图提示词是空的，无法生成。请先填写画面描述。")

    if anchor_index is None:
        anchor_index = guess_anchor_index(
            article=article, suggestion=suggestion, index=suggestion_index
        )

    provider = get_image_provider()

    record = GeneratedImage.objects.create(
        article=article,
        user=user,
        suggestion_index=suggestion_index,
        suggestion=suggestion or "",
        prompt=final_prompt,
        anchor_index=anchor_index,
        caption=truncate(caption, 200, ""),
        status=GeneratedImage.Status.RUNNING,
        provider=provider.name,
        model_name=getattr(settings, "IMAGE_MODEL", "") or "",
        sort_order=article.images.count(),
    )

    started = time.monotonic()
    result = provider.generate(final_prompt)
    duration_ms = int((time.monotonic() - started) * 1000)

    record.duration_ms = duration_ms

    if not result.ok:
        record.status = GeneratedImage.Status.FAILED
        record.error_message = result.message or result.error
        record.save(
            update_fields=["status", "error_message", "duration_ms", "updated_at"]
        )
        logger.warning(
            "配图生成失败 article_id=%s error=%s", article.id, result.error or "empty"
        )
        # ★ 把 provider 的中文说明原样抛给用户 —— 这类错误（key 错、模型名错、
        #   余额不足、提示词被拒）全都是**用户自己能改的**，
        #   糊成一句"生成失败"等于让他去猜。
        raise ImageProviderError(result.message or "配图生成失败。")

    data = result.data
    width, height = image_utils.probe(data)

    record.status = GeneratedImage.Status.SUCCEEDED
    record.content_type = image_utils.sniff_content_type(data)
    record.byte_size = len(data)
    record.width = width
    record.height = height
    record.revised_prompt = truncate(result.revised_prompt or "", 2000, "")
    record.error_message = ""

    # ★ 唯一的写文件点。
    #   失败路径（上面那个 if not result.ok）不碰文件 ——
    #   upload_to 每次调用都会生成一个新的 uuid4，多写一次就多一个孤儿文件。
    #
    # ★ save=False + 下面把 "image_file" 放进 update_fields，是**两件必须一起做**的事：
    #   save=False 阻止 FieldFile.save() 内部再触发一次 record.save()（那会写全表字段）；
    #   而 update_fields 里**必须**有 "image_file"，否则文件名不落库 ——
    #   表现是图片明明生成了、磁盘上有，列表里却全是"图片没了"。
    #   这是这次改造最隐蔽的一个坑。
    record.image_file.save(
        f"{record.id}{image_utils.extension_for(record.content_type)}",
        ContentFile(data),
        save=False,
    )
    record.save(
        update_fields=[
            "status",
            "image_file",
            "content_type",
            "byte_size",
            "width",
            "height",
            "revised_prompt",
            "error_message",
            "duration_ms",
            "updated_at",
        ]
    )

    logger.info(
        "配图生成成功 article_id=%s image_id=%s bytes=%s 耗时=%sms",
        article.id,
        record.id,
        record.byte_size,
        duration_ms,
    )
    return record


# ---------------------------------------------------------------------------
# 用户自己上传实拍图
# ---------------------------------------------------------------------------
def upload_image(
    *,
    article: Article,
    user,
    data: bytes,
    caption: str = "",
    anchor_index: int | None = None,
) -> GeneratedImage:
    """把用户自己上传的一张照片存成配图。

    存好之后它和 AI 配图**完全等价**：一样有图注、插入位置、能设封面，
    推送和「复制到公众号」走的也是同一条路（包括上传到微信换 mmbiz 地址
    那一步，见 wechat.services._rehost_images_best_effort）。

    ★ 和 generate_image 的三点不同，都是刻意的：

        1. **不查每日额度**。那条额度是防生图 API 烧钱的，上传自己的
           照片不花钱（详见 GeneratedImage.Source 的说明）。
           但**单篇上限照样查** —— 那条管的是"正文里图太多很难读"。
        2. **同步、没有 provider、不会失败在半路**。字节已经在手里了，
           没有 30~90 秒的等待，也没有"调 API 失败"这种状态。
        3. **字节来自浏览器，是不可信的**。必须验明是图片、必须去 EXIF，
           这两件事都收敛在 image_utils.normalize_uploaded_photo 里。

    ★ 不做的一件事：**不压缩到 1MB 以内**。
      那是上传到微信那一步的要求，由 normalize_for_wechat_body 负责。
      这里只保证落盘的图不是个 3MB 的巨物（缩到 1600px + quality 88）。
      分成两步是因为它们的**触发时机不同**：落盘一次，上传微信可能很多次
      （每张图只在第一次上传时压一次，之后复用 wechat_url）。
    """
    # ★ 单篇上限要查，每日额度不查 —— 见 docstring 第 1 点。
    enforce_article_limit(article=article)

    if anchor_index is None:
        # 用户没在界面上选位置时的默认值。
        # ★ 不走 guess_anchor_index()：那个函数是靠**配图建议的文字**猜的
        #   （"开头/封面/题图"之类关键词），而上传的图根本没有建议文字，
        #   传进去只会走到它的最后一条兜底（按序号均分），等于白绕一圈。
        #   实拍图默认放开头最合理 —— 通常是题图或现场图。
        anchor_index = 0

    normalized = image_utils.normalize_uploaded_photo(data)
    if normalized is None:
        # ★ 一句提示要同时覆盖"不是图片"和"太大了"两种情况，
        #   因为对用户来说这两件事的下一步动作是同一个：换一张再传。
        #   到底哪种，后端日志里有（normalize_uploaded_photo 会打 WARNING）。
        raise ImageUploadInvalidError(
            f"这个文件没能识别成图片。请传 JPG / PNG / WebP 格式的照片，"
            f"大小不超过 {image_utils.MAX_UPLOAD_BYTES // (1024 * 1024)}MB。"
        )
    content, content_type = normalized

    width, height = image_utils.probe(content)

    record = GeneratedImage.objects.create(
        article=article,
        user=user,
        # source 决定它会不会占每日额度（见 GeneratedImage.Source）
        source=GeneratedImage.Source.UPLOAD,
        # ★ suggestion_index / suggestion / prompt 全部留默认值。
        #   它们是"AI 配图建议"这条链路的字段，硬塞会给排查添乱：
        #   将来看到 prompt 是空的才知道这是上传的，而不是"生成时忘了填"。
        anchor_index=anchor_index,
        caption=truncate(caption, 200, ""),
        # 直接就是成功态：没有中间过程，也就不该有中间态可查
        status=GeneratedImage.Status.SUCCEEDED,
        # provider 留空（它不是生图服务生成的）。
        # 要判断"是不是上传的"请认 source，不要认这个字段。
        content_type=content_type,
        byte_size=len(content),
        width=width,
        height=height,
        # 同一位置的排序接着已有的往后排，不然会和 AI 图撞 sort_order
        sort_order=article.images.count(),
    )

    # ★ 写文件的方式和 generate_image 里那处**必须完全一致**：
    #   save=False 阻止 FieldFile.save() 内部再触发一次 record.save()；
    #   而 update_fields 里**必须**有 "image_file"，否则文件名不落库 ——
    #   表现是图片明明在磁盘上、列表里却全是"图片没了"。
    #
    # ★ 传给 save() 的名字里**刻意不含用户上传的原始文件名**：
    #   image_upload_to 只取它的扩展名，真正的路径是它自己生成的
    #   uuid4（那是 /media/ 这条链路上唯一的访问控制）。
    #   让用户的文件名参与进来没有任何好处，只有路径注入的风险面。
    record.image_file.save(
        f"{record.id}{image_utils.extension_for(content_type)}",
        ContentFile(content),
        save=False,
    )
    record.save(
        update_fields=[
            "image_file",
            "content_type",
            "byte_size",
            "width",
            "height",
            "updated_at",
        ]
    )

    logger.info(
        "实拍图已上传 article_id=%s image_id=%s bytes=%s",
        article.id,
        record.id,
        record.byte_size,
    )
    return record


# ---------------------------------------------------------------------------
# 编辑
# ---------------------------------------------------------------------------
# 允许通过 API 修改的字段。白名单，不是黑名单 ——
# 黑名单的话，将来给模型加一个字段就自动变成"可被用户改"，那是很危险的默认值。
EDITABLE_FIELDS = ("caption", "anchor_index", "sort_order", "is_cover")


@transaction.atomic
def update_image(*, image: GeneratedImage, data: dict) -> GeneratedImage:
    """改图注 / 插入位置 / 顺序 / 是否封面。"""
    changed: list[str] = []

    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "caption":
            value = truncate(value or "", 200, "")
        setattr(image, field, value)
        changed.append(field)

    if changed:
        image.save(update_fields=[*changed, "updated_at"])

    # ★ 设封面要把其他张的 is_cover 清掉。
    #   放在 save 之后：如果先清后设，中间出错就会变成"一张封面都没有"。
    if data.get("is_cover"):
        set_cover(image=image, is_cover=True)

    return image


@transaction.atomic
def set_cover(*, image: GeneratedImage, is_cover: bool = True) -> GeneratedImage:
    """把某张图设为封面（同一篇文章里只会有一张）。"""
    if is_cover:
        # 同一篇文章的其他图全部取消封面
        GeneratedImage.objects.filter(article=image.article).exclude(id=image.id).update(
            is_cover=False
        )
    GeneratedImage.objects.filter(id=image.id).update(is_cover=is_cover)
    image.is_cover = is_cover
    return image


def delete_image(*, image: GeneratedImage) -> None:
    """删除一张配图。

    不做软删除：配图是可再生的（大不了重新生成一张），
    而且留着会让"这篇到底有几张图"变得难以判断。
    真要保留，用户不点删除就行了。

    磁盘上的文件由 GeneratedImage 的 post_delete 信号清理，这里不用管 ——
    那样 Article 级联删除时也能一并收拾干净。
    """
    image_id = image.id
    image.delete()
    logger.info("配图已删除 image_id=%s", image_id)


def get_cover_image(article: Article, preferred_id=None) -> GeneratedImage | None:
    """挑出这篇文章要用作封面的图。

    优先级：接口显式指定 → is_cover=True 的那张 → 第一张成功的图。
    最后这个兜底很重要 —— 用户可能直接点"推送"而没设过封面，
    这时候不该报错让他回去点一下，拿第一张就好。
    """
    succeeded = get_article_images(article).filter(status=GeneratedImage.Status.SUCCEEDED)

    if preferred_id:
        explicit = succeeded.filter(id=preferred_id).first()
        if explicit:
            return explicit

    cover = succeeded.filter(is_cover=True).first()
    if cover:
        return cover

    return succeeded.first()
