"""
图片处理（Pillow）。

这个模块主要处理两件事：

**一、微信对图片的大小限制非常硬**（原本它是本模块存在的唯一原因）

    · 正文图（media/uploadimg）：只收 jpg/png，且**必须小于 1MB**，超了报 40009
    · 封面（material/add_material?type=thumb）：只收 **JPG**，且约 **64KB**

生图模型吐出来的图经常是 1.5~3MB 的 PNG，**直接上传必然失败**。
所以压缩不是一个"顺手优化"，而是推送到公众号这条链路上的**必经环节** ——
少了它，功能就是坏的。

**二、用户自己上传的照片不能直接信**（normalize_uploaded_photo）

    字节来自浏览器，必须先解码验明是图片、必须去掉 EXIF（里面有 GPS），
    再顺手缩到 1600px。详见那个函数的说明。

本模块所有函数**不抛异常**：图片坏掉时返回原数据或 None，
由上层把"这张图有问题"变成一句中文提示，而不是一个 500。
"""

from __future__ import annotations

import io
import logging

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Pillow 默认有个"解压炸弹"防护（约 89M 像素）。生图模型不会输出这么大的图，
# 保持默认即可 —— 显式写出来是为了说明我们没有关掉它。

# 微信正文图的实际上限是 1MB。这里留一点余量：
# 我们数的是字节数，微信那边怎么数没人保证，卡在 1048576 边缘很容易偶发失败。
WECHAT_BODY_MAX_BYTES = 1_000_000

# 封面（thumb）的实际上限约 64KB。这个限制非常容易踩 ——
# 900x500 的 JPG 在 quality=85 时通常有 90~150KB，必须真的循环压下去。
WECHAT_THUMB_MAX_BYTES = 64 * 1024

# 逐级降质量的档位。先掉画质、后掉尺寸：
# 对插画类图片来说，轻微掉画质比缩小分辨率更不容易被看出来。
_QUALITY_STEPS = (85, 78, 70, 62, 55, 48, 40)

# ---- 用户上传的实拍图 ----
# 接收上限（**进入 Django 之前**的那道闸是 nginx 的 client_max_body_size）。
# ★ 前端会先用 canvas 把照片压到 1600px 再传，正常不超过 1MB。
#   这个上限是给"前端压缩没生效"（老浏览器、canvas 被禁用）兜底的。
#   两道闸门都要有，各挡各的：nginx 那道挡的是"别把 10MB 灌进我的进程"，
#   这道挡的是"别把 10MB 写进磁盘"。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 落盘前统一把长边缩到这个像素数。
# ★ 公众号正文图的显示宽度约 677px（视网膜屏按 2x 算就是 1354），
#   1600 足够清晰；再大只是白占磁盘、白耗用户流量。
MAX_UPLOAD_EDGE = 1600

# 实拍图的 JPEG 质量。比正文图那套档位(最高 85)高一点：
# 照片的细节比插画多，压过头一眼能看出来（天空会出现色带）。
_UPLOAD_JPEG_QUALITY = 88


def _open(data: bytes) -> Image.Image | None:
    """把字节读成 Pillow 图像；读不了就返回 None。"""
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        # load() 会立刻真正解码。不调用的话，坏图要到 save() 时才炸，
        # 那样错误点会跑到很远的地方，很难查。
        image.load()
        return image
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("图片无法解码: %s", exc)
        return None
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        # ★ 这两个**必须单独列出来**，它们不是 OSError 的子类
        #   （DecompressionBombError 直接继承 Exception），上面那条抓不住。
        #
        #   触发条件：字节里的文件头声称自己是一张巨大的图（Pillow 的阈值是
        #   约 8900 万像素，超过 2 倍直接抛错）。这里读的字节来自**生图服务**，
        #   不是我们自己的文件 —— 一个坏掉或恶意的中转站返回这么一坨，
        #   就会让 DecompressionBombError 一路穿过本模块"绝不抛异常"的契约。
        #   本模块的每个函数（probe / normalize_for_wechat_body /
        #   make_wechat_thumb）都声明了不抛异常，漏掉这两个类等于那句声明是假的：
        #   真出事时错误会跑到很远的地方，变成一个看不出原因的中文提示。
        #
        #   注意 DecompressionBombWarning 只在"警告被当成错误"（-W error）时
        #   才会抛出来，平时不会 —— 但它同样应该被归到"这张图打不开"，
        #   归到这里才是一致的。
        logger.warning("图片尺寸异常，疑似解压炸弹: %s", exc)
        return None


def _to_rgb(image: Image.Image) -> Image.Image:
    """转成 JPEG 能存的 RGB 模式。

    两个必须处理的坑：
        · 带透明通道的 PNG（RGBA/LA/P）直接存 JPEG 会抛 OSError
        · 调色板图（P）要先转 RGBA 再合成，否则透明区会变成黑块
    透明区域统一铺白底 —— 公众号正文是白底，合成黑块会非常难看。
    """
    if image.mode == "RGB":
        return image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas

    return image.convert("RGB")


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def normalize_for_wechat_body(data: bytes) -> tuple[bytes, str]:
    """把图片压到微信 uploadimg 能收的大小。

    返回 (图片字节, MIME 类型)。

    ★ 已经是 JPEG 且本来就够小的，**原样返回**。
      重新编码一次会白白掉一层画质（JPEG 是有损的，二次压缩的损失是叠加的），
      而这种情况其实很常见 —— 我们请求的就是 jpeg 输出。
    """
    if not data:
        return b"", "image/jpeg"

    if len(data) <= WECHAT_BODY_MAX_BYTES and data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"

    image = _open(data)
    if image is None:
        # 解不开就把原数据原样交出去。
        # 不在这里报错，是因为"压缩失败"和"图片本身有问题"是两件事，
        # 让结构更清楚的上一层去决定怎么处理。
        return data, "image/jpeg"

    rgb = _to_rgb(image)

    # 第一轮：只降质量，不动尺寸
    for quality in _QUALITY_STEPS:
        buffer = _encode_jpeg(rgb, quality)
        if len(buffer) <= WECHAT_BODY_MAX_BYTES:
            logger.info("正文图压缩完成 %s -> %s 字节 (quality=%s)", len(data), len(buffer), quality)
            return buffer, "image/jpeg"

    # 第二轮：质量降到 40 还超，说明尺寸太大，等比缩小
    scale = 0.8
    while scale >= 0.3:
        resized = rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
            Image.LANCZOS,
        )
        buffer = _encode_jpeg(resized, 70)
        if len(buffer) <= WECHAT_BODY_MAX_BYTES:
            logger.info(
                "正文图缩放后压缩完成 %s -> %s 字节 (scale=%.2f)",
                len(data),
                len(buffer),
                scale,
            )
            return buffer, "image/jpeg"
        scale -= 0.15

    # 到这一步属于异常情况（正常插画不可能），但仍然返回一个结果而不是抛异常，
    # 让上传去撞微信的报错，日志里能看到真实原因。
    logger.error("正文图压缩后仍然超过 %s 字节", WECHAT_BODY_MAX_BYTES)
    return _encode_jpeg(rgb, 40), "image/jpeg"


def normalize_uploaded_photo(data: bytes) -> tuple[bytes, str] | None:
    """把用户上传的照片整成一张可以落盘的 JPEG。整不了返回 None。

    ★★ 为什么用户上传的图**必须**整个重编码一遍，
        而不能像生图那样"够小就原样存"（normalize_for_wechat_body 的做法）：

      1. **必须验明它真的是图片。** 生图那条路的字节来自我们自己的
         provider，可以信；这里来自浏览器，一个改了扩展名的 .php
         也能传上来。_open() 会真的解码（还调了 load()），
         解不开就是 None —— 这是整条链路上**唯一**的验证点。
         扩展名和浏览器报的 Content-Type 都不可信，一个都不看。

      2. **必须去掉 EXIF。** 手机拍的照片里带 GPS 坐标，
         而这些图**最终是要发到公众号上公开的**。不重编码就等于
         把拍照的人当时站在哪儿一起发出去了 —— 而且这种泄漏
         用户完全不会察觉，也不会有人来报 bug。
         Pillow 重编码时默认不写 EXIF，所以这一步顺带就做到了。
         ★ 别为了"保留拍摄信息"去加 exif=... —— 那正是要扔掉的东西。

      3. 手机照片动辄 4000px 宽、5MB 以上，而微信正文图硬性要求
         小于 1MB。反正要压，不如压在这一步：磁盘和带宽都省一遍。

    ★ 永远输出 JPEG：实拍图没有透明通道，PNG 只会更大。
      统一成 jpg 也少一条分支（微信对 jpg/png 都收，本来也不用分）。
    """
    if not data:
        return None

    if len(data) > MAX_UPLOAD_BYTES:
        # ★ 这里不抛异常，和本模块其他函数一个态度：
        #   由上层把 None 变成一句中文提示。
        logger.warning("上传的图片超过上限 %s 字节，实际 %s", MAX_UPLOAD_BYTES, len(data))
        return None

    image = _open(data)
    if image is None:
        # 到这一步说明字节根本不是能解码的图片（或者是个解压炸弹）
        logger.warning("上传的文件不是能识别的图片，%s 字节", len(data))
        return None

    rgb = _to_rgb(image)

    # 先缩尺寸再压质量 —— 和 normalize_for_wechat_body 同一个顺序，
    # 但对实拍图来说"缩尺寸"这一步几乎总会触发：1600px 的实拍图
    # 在 quality=88 时通常只有 300~600KB，够发公众号了，
    # 没必要为了再省几十 KB 去牺牲画质。
    longest = max(rgb.width, rgb.height)
    if longest > MAX_UPLOAD_EDGE:
        scale = MAX_UPLOAD_EDGE / longest
        rgb = rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
            Image.LANCZOS,
        )

    buffer = _encode_jpeg(rgb, _UPLOAD_JPEG_QUALITY)

    # 噪声极多的照片（星野、树叶、水面）偶尔会超过 1MB。
    # ★ 这里只降一档质量，**不重复实现一遍完整的压缩循环** ——
    #   normalize_for_wechat_body 在上传微信那一步还会再压一次，
    #   那才是硬要求所在的地方。这里只要保证"落盘的图不是个 3MB 的巨物"。
    if len(buffer) > WECHAT_BODY_MAX_BYTES:
        buffer = _encode_jpeg(rgb, 70)

    logger.info(
        "实拍图已处理 %s -> %s 字节 (%sx%s)",
        len(data),
        len(buffer),
        rgb.width,
        rgb.height,
    )
    return buffer, "image/jpeg"


def make_wechat_thumb(
    data: bytes,
    *,
    width: int = 900,
    height: int = 500,
    max_bytes: int = WECHAT_THUMB_MAX_BYTES,
) -> bytes | None:
    """生成公众号封面图：裁成 900x500 的 JPG，并压到 64KB 以内。

    返回 JPEG 字节；图片坏了返回 None。

    ★ 封面必须**单独生成**，不能拿正文图凑合，原因有三：
        1. thumb 只收 JPG，正文图可能是 PNG
        2. thumb 只允许约 64KB
        3. 封面在公众号里是固定比例裁切显示的，给一张 1024x1536 的竖图，
           微信会自己裁中间一块，经常把画面的主体裁掉，非常难看。
           我们自己按 900x500（1.8:1，和微信的封面展示比例接近）裁，
           并且**从中心偏上**取景 —— 人物的头、标题性的构图通常在上半部分。
    """
    image = _open(data)
    if image is None:
        return None

    rgb = _to_rgb(image)
    thumb = _cover_crop(rgb, width, height)

    # 封面尺寸小，通常一轮就能过；循环是防御性的 ——
    # 万一遇到噪声极多（比如星空、树叶）的图，同一档质量也可能超标。
    for quality in _QUALITY_STEPS:
        buffer = _encode_jpeg(thumb, quality)
        if len(buffer) <= max_bytes:
            return buffer

    # 还是超：继续缩小尺寸，依次 720x400 / 585x325 / 450x250
    for scale in (0.8, 0.65, 0.5):
        resized = thumb.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS
        )
        buffer = _encode_jpeg(resized, 60)
        if len(buffer) <= max_bytes:
            return buffer

    # 正常插画不可能走到这里。走到了也返回一个结果，让上传去撞微信的报错，
    # 好过在这里抛异常把整个推送流程掀掉 —— 后端日志里能看到真实原因。
    logger.error("封面图压缩后仍然超过 %s 字节", max_bytes)
    return _encode_jpeg(thumb.resize((width // 2, height // 2), Image.LANCZOS), 40)


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """等比缩放到刚好盖住目标框，再从**中心偏上**裁下来。"""
    target_ratio = width / height
    src_ratio = image.width / image.height

    if src_ratio > target_ratio:
        # 原图更宽：先按高度缩放，再裁左右
        new_height = height
        new_width = max(width, int(round(height * src_ratio)))
    else:
        # 原图更高：先按宽度缩放，再裁上下
        new_width = width
        new_height = max(height, int(round(width / src_ratio)))

    resized = image.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - width) // 2
    # 垂直方向偏上取景：留 35% 的上方空间，而不是 50%。
    # 画面主体（主体、标题性构图）通常在上半部分，
    # 正中间裁经常把人的头裁掉、把画面的重点裁没。
    top = max(0, int((new_height - height) * 0.35))
    return resized.crop((left, top, left + width, top + height))


def probe(data: bytes) -> tuple[int | None, int | None]:
    """读出图片的宽高。读不出来返回 (None, None)。

    宽高只用来在界面上显示和排版，拿不到不该让整个流程失败。
    """
    image = _open(data)
    if image is None:
        return None, None
    return image.width, image.height


def extension_for(content_type: str) -> str:
    """给落盘的文件名挑一个扩展名。

    ★ 必须和 sniff_content_type() 认出来的 MIME 保持一致：
      扩展名和真实字节对不上时，浏览器按扩展名猜 MIME，图会显示成破图。
    ★ 认不出来时按 .jpg —— 和 sniff_content_type 的兜底一致。
    """
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get((content_type or "").lower(), ".jpg")


def sniff_content_type(data: bytes) -> str:
    """从文件头猜 MIME 类型。

    不信任生图服务返回的 content-type，也不用 Pillow 的 format
    （那要解一遍图，而我们往往只是想知道"这到底是 PNG 还是 JPEG"）。
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
