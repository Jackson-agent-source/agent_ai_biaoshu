"""
图片处理测试（Pillow）。

★ 这个模块测的是**微信的硬性尺寸限制**，不是"顺手做的优化"：

    正文图（media/uploadimg）：只收 jpg/png，且必须 < 1MB，超了报 40009
    封面（material/add_material?type=thumb）：只收 JPG，且约 64KB

    生图模型吐出来的是 1.5~3MB 的图，**直接上传必然失败**。
    少了这一层压缩，整个推送功能就是坏的 —— 而且失败信息只有一句
    "微信接口返回错误 40009"，不知道的人根本查不到是尺寸问题。

★ 这些函数全部**不抛异常**（坏了返回原数据或 None），
  这条契约也要钉住：压缩失败不该让整个推送流程抛 500。
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from apps.imaging.image_utils import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_EDGE,
    WECHAT_BODY_MAX_BYTES,
    WECHAT_THUMB_MAX_BYTES,
    extension_for,
    make_wechat_thumb,
    normalize_for_wechat_body,
    normalize_uploaded_photo,
    probe,
    sniff_content_type,
)
from apps.imaging.tests.conftest import make_image_bytes


def noisy_png(width: int = 1000, height: int = 1000) -> bytes:
    """造一张**很大**的 PNG（随机噪声）。

    ★ 必须用噪声而不是纯色：纯色图 JPEG/PNG 都能压到几 KB，
      根本触发不了"超过 1MB 要压缩"这条路径 —— 测试会全绿地漏掉 bug。
      噪声图不可压缩，才能真的把尺寸问题暴露出来。
    """
    raw = os.urandom(width * height * 3)
    buffer = io.BytesIO()
    Image.frombytes("RGB", (width, height), raw).save(buffer, format="PNG")
    return buffer.getvalue()


def open_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


class TestNormalizeForWechatBody:
    def test_empty_input_returns_empty(self):
        assert normalize_for_wechat_body(b"") == (b"", "image/jpeg")

    def test_small_jpeg_is_passed_through_untouched(self):
        """★ 已经够小的 JPEG **原样返回**，不重新编码。

        JPEG 是有损格式，二次压缩的损失是**叠加**的 ——
        白白掉一层画质换不到任何好处。而我们请求的就是 jpeg 输出，
        所以这条路径其实是常态，不是边缘情况。
        """
        data = make_image_bytes(600, 400, fmt="JPEG")

        result, content_type = normalize_for_wechat_body(data)

        assert result == data  # 一个字节都没动
        assert content_type == "image/jpeg"

    def test_large_image_is_compressed_under_limit(self):
        """★ 超过 1MB 的图必须被压到 1MB 以内，否则微信直接拒收（40009）。"""
        data = noisy_png()
        assert len(data) > WECHAT_BODY_MAX_BYTES, "测试素材没造够大，这条用例就白测了"

        result, content_type = normalize_for_wechat_body(data)

        assert content_type == "image/jpeg"
        assert len(result) <= WECHAT_BODY_MAX_BYTES
        # 压完还得是能打开的图（压坏了等于发一张破图上去）
        assert open_image(result).width > 0

    def test_png_with_alpha_is_flattened_to_white(self):
        """★ 带透明通道的 PNG 直接存 JPEG 会抛 OSError。

        必须铺白底。铺黑底（不合成直接 convert）在公众号的白底正文里
        会出现一圈难看的黑边。
        """
        buffer = io.BytesIO()
        Image.new("RGBA", (300, 200), (255, 0, 0, 0)).save(buffer, format="PNG")
        data = buffer.getvalue()

        result, _ = normalize_for_wechat_body(data)

        image = open_image(result)
        assert image.mode == "RGB"
        # 完全透明的区域应该变成白色，不是黑色
        assert image.getpixel((10, 10)) == (255, 255, 255)

    def test_garbage_bytes_are_returned_as_is(self):
        """★ 解不开的字节原样返回，**不抛异常**。

        "压缩失败"和"图片本身有问题"是两件事。在这里抛异常会让
        上层看到一个 500，而真正的原因（生图服务返回了一坨垃圾）
        反而被埋掉了。
        """
        garbage = b"\x00\x01\x02 not an image at all"

        result, content_type = normalize_for_wechat_body(garbage)

        assert result == garbage
        assert content_type == "image/jpeg"


class TestMakeWechatThumb:
    def test_crops_to_wechat_cover_ratio(self):
        """★ 封面要自己裁成 900x500，不能把 1024x1536 的竖图直接丢给微信。

        微信会自己裁中间一块，经常把画面主体（人的头、标题性构图）裁掉。
        """
        thumb = make_wechat_thumb(make_image_bytes(800, 600))

        assert thumb is not None
        assert open_image(thumb).size == (900, 500)
        assert len(thumb) <= WECHAT_THUMB_MAX_BYTES

    def test_output_is_always_jpeg(self):
        """★ thumb 接口**只收 JPG**。PNG 进去也必须 JPG 出来。"""
        thumb = make_wechat_thumb(make_image_bytes(800, 600, fmt="PNG"))

        assert thumb is not None
        assert thumb[:3] == b"\xff\xd8\xff", "封面不是 JPEG 字节"

    def test_very_noisy_image_still_returns_something(self):
        """★ 噪声极大的图（星空、树叶）压不到 64KB 时仍然要返回结果。

        契约是"尽力而为，绝不抛异常"：真压不下去就让上传去撞微信的报错，
        服务器日志里能看到真实原因 —— 好过在这里把整个推送掀掉。
        """
        thumb = make_wechat_thumb(noisy_png(900, 900))

        assert thumb is not None
        assert thumb[:3] == b"\xff\xd8\xff"
        assert open_image(thumb).width > 0

    def test_garbage_returns_none(self):
        """图片坏了返回 None（而不是抛异常），由上层变成一句中文提示。"""
        assert make_wechat_thumb(b"\x00\x01\x02 garbage") is None

    def test_empty_returns_none(self):
        assert make_wechat_thumb(b"") is None


class TestProbe:
    def test_reads_real_dimensions(self):
        assert probe(make_image_bytes(640, 480)) == (640, 480)

    def test_garbage_returns_none_pair(self):
        """宽高只是界面上显示用的，读不出来不该让流程失败。"""
        assert probe(b"not an image") == (None, None)

    def test_empty_returns_none_pair(self):
        assert probe(b"") == (None, None)


class TestSniffContentType:
    def test_recognizes_each_format_by_magic_bytes(self):
        """按文件头认格式，不信任生图服务返回的 content-type。"""
        assert sniff_content_type(make_image_bytes(fmt="JPEG")) == "image/jpeg"
        assert sniff_content_type(make_image_bytes(fmt="PNG")) == "image/png"
        assert sniff_content_type(make_image_bytes(fmt="GIF")) == "image/gif"
        assert sniff_content_type(make_image_bytes(fmt="WEBP")) == "image/webp"

    def test_unknown_falls_back_to_jpeg(self):
        assert sniff_content_type(b"\x00\x01\x02") == "image/jpeg"
        assert sniff_content_type(b"") == "image/jpeg"


class TestExtensionFor:
    def test_matches_sniff_content_type(self):
        """★ 扩展名必须和 sniff_content_type 认出来的 MIME 一致。

        对不上时浏览器按扩展名猜 MIME，一个 .jpg 里装着 PNG 字节会显示成破图
        （部分浏览器会"猜回来"，所以这种 bug 常常只在某些浏览器上出现，
        极难复现）。所以这条一致性断言比单测映射表更有价值。
        """
        for fmt in ("JPEG", "PNG", "GIF", "WEBP"):
            data = make_image_bytes(fmt=fmt)
            content_type = sniff_content_type(data)
            expected = "." + {"image/jpeg": "jpg", "image/png": "png",
                              "image/gif": "gif", "image/webp": "webp"}[content_type]
            assert extension_for(content_type) == expected

    def test_unknown_type_falls_back_to_jpg(self):
        """兜底要和 sniff_content_type 的兜底一致（都是 jpeg）。"""
        assert extension_for("") == ".jpg"
        assert extension_for("application/octet-stream") == ".jpg"
        assert sniff_content_type(b"\x00") == "image/jpeg"

    def test_case_insensitive(self):
        assert extension_for("IMAGE/PNG") == ".png"


# ---------------------------------------------------------------------------
# 用户上传的照片
# ---------------------------------------------------------------------------
def photo_with_gps_exif(width: int = 800, height: int = 600) -> bytes:
    """造一张**带 GPS 坐标**的 JPEG，冒充手机拍的照片。

    ★ 为什么测试里非要真的写 GPS 进去，而不是随便塞个标签：
      要验的正是"照片里的定位信息不会跟着发到公众号上"。
      塞一个无关的标签只能证明"某些 EXIF 没了"，证明不了那一件事。
      （写 GPS 需要 IFDRational，直接写元组会在 tobytes() 时
        TypeError: bad operand type for abs(): 'tuple'。）
    """
    from PIL.TiffImagePlugin import IFDRational

    exif = Image.Exif()
    exif[0x010F] = "TestPhone"  # Make
    exif[0x0110] = "TestModel"  # Model
    exif[0x8825] = {  # GPSInfo
        1: "N",
        2: (IFDRational(39, 1), IFDRational(54, 1), IFDRational(0, 1)),
        3: "E",
        4: (IFDRational(116, 1), IFDRational(23, 1), IFDRational(0, 1)),
    }

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 90)).save(
        buffer, format="JPEG", exif=exif.tobytes()
    )
    return buffer.getvalue()


class TestNormalizeUploadedPhoto:
    """★ 用户上传的字节是**不可信**的，这个函数是唯一的验证点。

    两条底线，各自一条测试：
        1. 不是图片的字节必须被拒绝（返回 None），不能落盘
        2. 定位信息必须被清掉 —— 这些图最终要发到公众号上公开
    """

    def test_rejects_non_image_bytes(self):
        """一个改了扩展名的 PHP 文件不能落盘。

        ★ 这条是**安全**断言，不是格式检查：/media/ 和网站同源，
          让非图片字节落进去就是给自己开后门。
          而且必须真的解码过（PIL 的 load()）才算验过 ——
          只看文件头的话，一个 JPEG 头 + 后面挂 PHP 的文件能骗过去。
        """
        assert normalize_uploaded_photo(b"<?php system($_GET['c']); ?>") is None
        assert normalize_uploaded_photo(b"not an image at all") is None
        # 只有文件头对、后面是垃圾的也要拒
        assert normalize_uploaded_photo(b"\xff\xd8\xff" + b"\x00" * 100) is None

    def test_rejects_empty(self):
        assert normalize_uploaded_photo(b"") is None

    def test_rejects_oversized_before_decoding(self):
        """超过上限直接拒，不去解码它（省掉一次可能很贵的解码）。"""
        assert normalize_uploaded_photo(b"x" * (MAX_UPLOAD_BYTES + 1)) is None

    def test_strips_gps_and_all_exif(self):
        """★★ 上传后必须一点 EXIF 都不剩。

        这是本次改动里**唯一一条用户自己永远发现不了的**风险：
        手机照片带 GPS 坐标，而他不会知道自己的位置被一起发出去了。
        Pillow 重编码时不写 EXIF，所以这一步天然做到 ——
        这条测试就是钉住"别为了保留拍摄信息去加 exif=..."。
        """
        source = photo_with_gps_exif()
        # 先确认造的样本真的是带 GPS 的，否则这条测试是假绿
        assert 0x8825 in open_image(source).getexif()

        result = normalize_uploaded_photo(source)

        assert result is not None
        content, content_type = result
        assert content_type == "image/jpeg"
        assert open_image(content).getexif() == {}

    def test_downscales_long_edge(self):
        """4000px 的手机照片要缩到 MAX_UPLOAD_EDGE，否则白占磁盘和流量。"""
        source = photo_with_gps_exif(width=4000, height=3000)

        result = normalize_uploaded_photo(source)

        assert result is not None
        image = open_image(result[0])
        assert max(image.width, image.height) == MAX_UPLOAD_EDGE
        # 等比：宽高比不能变（变形比模糊更难看）
        assert image.width / image.height == pytest.approx(4000 / 3000, rel=0.01)

    def test_small_photo_is_not_enlarged(self):
        """比上限小的图**不要**放大 —— 放大只会糊，不会更清楚。"""
        source = photo_with_gps_exif(width=800, height=600)

        result = normalize_uploaded_photo(source)

        assert result is not None
        image = open_image(result[0])
        assert (image.width, image.height) == (800, 600)

    def test_always_outputs_jpeg(self):
        """PNG 上传也统一转成 JPEG（实拍图没有透明通道，PNG 只会更大）。"""
        png = make_image_bytes(fmt="PNG", width=300, height=200)

        result = normalize_uploaded_photo(png)

        assert result is not None
        content, content_type = result
        assert content_type == "image/jpeg"
        assert content[:3] == b"\xff\xd8\xff"

    def test_output_stays_under_wechat_body_limit(self):
        """落盘的图不该是个巨物 —— 噪声照片也要能压到 1MB 以内。

        噪声是**最坏情况**（不可压缩）。真实照片比它好压得多，
        所以这条过了，实拍图就都不会在上传微信那一步被 40009 拒。
        """
        source = noisy_png(width=2000, height=1500)

        result = normalize_uploaded_photo(source)

        assert result is not None
        assert len(result[0]) <= WECHAT_BODY_MAX_BYTES
