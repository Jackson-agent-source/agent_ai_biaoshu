"""
上传实拍图接口测试。

    POST /api/v1/articles/<id>/images/upload   （multipart，字段名 file）

★★ 这条路由和「生成一张 AI 配图」最大的不同：**字节来自浏览器**。

    生成那条路的字节来自我们自己的 provider，可以信；
    这条路的字节是用户传上来的，**一个都不能信**。

    所以下面这一组的重点不是 happy path，而是三件用户自己
    既不会察觉、也不会来报 bug 的事：

    1. **不能落盘非图片字节**。/media/ 和网站**同源** ——
       一个 .svg / .html 落进去就是一个存储型 XSS 入口。
       改了扩展名的 PHP 也一样，不能因为"它长得像图片"就放行。

    2. **必须清掉 EXIF**。手机照片带 GPS 坐标，而这些图
       **最终要发到公众号上公开**。不处理就等于把拍照的人
       当时站在哪儿一起发出去了，而这件事没有任何症状。

    3. **上传不能吃掉每日额度**。那条额度是防生图 API 烧钱的，
       用户传自己的照片一分不花。跟着占额度的话，他会看到
       "你今天已生成 8 张配图，达到上限" —— 而他一张都没生成过。

    另外单独盯住两类接口一致性（它们错了不会报错，只会行为不对）：
    · **单篇上限照样要管**（和花钱无关，那条管的是"正文图太多很难读"）
    · **上传的图必须能走通推送/复制那条老路**（否则这个功能等于白做）
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.imaging.models import GeneratedImage
from apps.imaging.tests.test_image_utils import photo_with_gps_exif, noisy_png
from apps.imaging.tests.conftest import make_image_bytes

# ★ 硬编码 URL，路由被改坏时测试才会红（和同目录其他测试文件一致）
ARTICLE_UPLOAD_URL = "/api/v1/articles/{article_id}/images/upload"
IMAGE_DETAIL_URL = "/api/v1/images/{image_id}"

# 复制到公众号那条路 —— 用来验"上传的图确实能发出去"，
# 这是这个功能存在的全部意义，不验等于没做。
COPY_HTML_URL = "/api/v1/articles/{article_id}/wechat-html"

pytestmark = pytest.mark.django_db


def upload_url(article) -> str:
    return ARTICLE_UPLOAD_URL.format(article_id=article.id)


def photo_file(data: bytes | None = None, name: str = "photo.jpg") -> SimpleUploadedFile:
    """包一个 multipart 用的文件对象。

    ★ content_type 这里写什么都无所谓 —— 后端**刻意不看它**
      （浏览器报的 MIME 和扩展名一样不可信）。
      下面有一条专门用错误的 content_type 来钉这件事。
    """
    return SimpleUploadedFile(
        name, data if data is not None else make_image_bytes(), content_type="image/jpeg"
    )


def upload(client, article, data: bytes | None = None, **extra):
    return client.post(
        upload_url(article),
        {"file": photo_file(data), **extra},
        format="multipart",
    )


# ===========================================================================
# 基本形状
# ===========================================================================
class TestUploadPhoto:
    def test_requires_login(self, api_client, article_for_images):
        assert (
            api_client.post(upload_url(article_for_images), {}, format="multipart").status_code
            == 401
        )

    def test_uploads_and_returns_usable_image(self, auth_client, article_for_images):
        """上传成功要返回一张**立刻可用**的图：直接是成功态，带 file_url 和尺寸。"""
        response = upload(auth_client, article_for_images)

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["success"] is True
        data = body["data"]

        assert data["source"] == "upload"
        assert data["source_display"] == "自己上传"
        # ★ 直接就是成功态：上传没有中间过程，也就不该有中间态可查
        assert data["status"] == "succeeded"
        assert data["file_url"].startswith("/media/")
        assert data["width"] and data["height"]
        assert data["byte_size"] > 0
        # 默认放在文章开头（实拍图通常是题图/现场图）
        assert data["anchor_index"] == 0

    def test_file_lands_on_disk_and_is_deletable(self, auth_client, article_for_images):
        """图片要真的落盘，而且删记录时文件跟着删（复用 post_delete 信号）。"""
        image = GeneratedImage.objects.get(
            id=upload(auth_client, article_for_images).json()["data"]["id"]
        )
        assert image.image_file.storage.exists(image.image_file.name)

        auth_client.delete(IMAGE_DETAIL_URL.format(image_id=image.id))

        assert not image.image_file.storage.exists(image.image_file.name)

    def test_stored_path_does_not_contain_original_filename(
        self, auth_client, article_for_images
    ):
        """★★ 落盘路径里**不能**出现用户上传的文件名。

        /media/ 这条链路上唯一的访问控制就是"路径猜不出来"
        （见 GeneratedImage.image_upload_to 的说明）。
        让用户的文件名参与进来，等于把这段不可猜性交给用户去决定 ——
        他能传一个叫 ../../../etc/passwd 的文件，也能传一个
        可枚举的名字。而这条路上没有任何别的东西拦着。
        """
        response = auth_client.post(
            upload_url(article_for_images),
            {"file": photo_file(name="my-holiday-photo-2026.jpg")},
            format="multipart",
        )
        assert response.status_code == 201

        image = GeneratedImage.objects.get(id=response.json()["data"]["id"])
        assert "my-holiday-photo" not in image.image_file.name
        # 扩展名仍然来自真实内容（jpg 进 jpg 出），不然浏览器会显示破图
        assert image.image_file.name.endswith(".jpg")

    def test_accepts_caption_and_anchor(self, auth_client, article_for_images):
        response = upload(
            auth_client, article_for_images, caption="现场照片", anchor_index=2
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["caption"] == "现场照片"
        assert data["anchor_index"] == 2

    def test_rejects_anchor_beyond_section_count(self, auth_client, article_for_images):
        """位置超出小节数要报错。

        ★ 和生成那条路同一个规则：超范围的 anchor_index 会让图在渲染时
          **无处可插**（静默丢掉），用户看到的是"我明明传了图，怎么没有"。
          不如在这里直接说清楚。文章有 3 个小节，所以 4 是非法的。
        """
        response = upload(auth_client, article_for_images, anchor_index=4)

        assert response.status_code == 400
        assert "只有 3 个小节" in response.json()["error"]["message"]


# ===========================================================================
# 不可信的字节
# ===========================================================================
class TestRejectsUntrustedBytes:
    """★★ 这一组全是安全断言。每一条都对应一个真实能被利用的入口。"""

    def test_rejects_non_image_bytes(self, auth_client, article_for_images):
        """改了扩展名的 PHP / HTML 不能落盘。

        /media/ 和网站**同源**，一个能执行的字节落进去就是存储型 XSS。
        扩展名白名单挡的是"用 .svg/.html 落盘"，但一个真名叫
        evil.php 的文件改成 .jpg 之后同样危险 —— 所以必须**解码验明**，
        而不是看名字和 Content-Type。
        """
        response = upload(auth_client, article_for_images, b"<?php system($_GET['c']); ?>")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "IMAGE_UPLOAD_INVALID"
        # ★ 关键：一条记录都不该留下，磁盘上也不该有文件
        assert GeneratedImage.objects.count() == 0

    def test_rejects_file_that_only_has_a_jpeg_header(self, auth_client, article_for_images):
        """只有文件头对、后面是垃圾的也要拒。

        这条钉的是"必须真的解码过（PIL 的 load()）"——
        只看前 3 个字节的实现能过上面那条，会在这条红。
        """
        response = upload(auth_client, article_for_images, b"\xff\xd8\xff" + b"\x00" * 500)

        assert response.status_code == 400
        assert GeneratedImage.objects.count() == 0

    def test_rejects_empty_file(self, auth_client, article_for_images):
        response = auth_client.post(
            upload_url(article_for_images), {"file": photo_file(b"")}, format="multipart"
        )

        assert response.status_code == 400
        assert "空" in response.json()["error"]["message"]

    def test_missing_file_field_gives_a_clear_message(self, auth_client, article_for_images):
        """整个 file 字段都没带时要有一句能看懂的话，而不是 KeyError/500。"""
        response = auth_client.post(
            upload_url(article_for_images), {"caption": "忘了带图"}, format="multipart"
        )

        assert response.status_code == 400
        assert "file" in response.json()["error"]["message"]

    def test_ignores_client_supplied_content_type(self, auth_client, article_for_images):
        """浏览器说的 Content-Type 一律不信 —— 真的图片就收，哪怕它说是文本。"""
        response = auth_client.post(
            upload_url(article_for_images),
            {
                "file": SimpleUploadedFile(
                    "photo.jpg", make_image_bytes(), content_type="application/octet-stream"
                )
            },
            format="multipart",
        )

        assert response.status_code == 201
        # 落库的 content_type 是**嗅探出来**的，不是客户端报的
        assert response.json()["data"]["content_type"] == "image/jpeg"

    def test_strips_gps_exif(self, auth_client, article_for_images):
        """★★ 上传带 GPS 的照片后，落盘的文件里不能有任何 EXIF。

        这是整个功能里**唯一一条用户永远不会察觉**的隐私风险：
        手机照片带定位，而这些图最终要发到公众号上公开。
        不重编码就等于把"拍照的人当时站在哪儿"一起发出去了。

        （底层机制在 image_utils.normalize_uploaded_photo 里，
          单元的 EXIF 断言在 test_image_utils.py；这里验的是
          **走完整个 HTTP 接口之后**磁盘上那份文件是干净的 ——
          中间任何一步引入"顺手保留一下原图"都会被这条抓住。）
        """
        from PIL import Image as PILImage
        import io as _io

        source = photo_with_gps_exif()
        assert 0x8825 in PILImage.open(_io.BytesIO(source)).getexif()  # 样本确实带 GPS

        response = upload(auth_client, article_for_images, source)
        assert response.status_code == 201

        image = GeneratedImage.objects.get(id=response.json()["data"]["id"])
        with image.image_file.open("rb") as fh:
            stored = fh.read()
        assert PILImage.open(_io.BytesIO(stored)).getexif() == {}


# ===========================================================================
# 额度与上限
# ===========================================================================
class TestQuotaAndLimit:
    def test_upload_does_not_consume_daily_quota(self, auth_client, article_for_images):
        """★★ 上传不占每日额度。

        那条额度数的是"今天调了几次生图 API"，它的全部目的是防烧钱。
        用户传自己的照片一分钱不花，跟着占额度的话他会看到
        "你今天已生成 N 张配图，达到上限"，而他一张都没生成过 ——
        这句提示会让他完全不知道该做什么。
        """
        with override_settings(IMAGE_DAILY_QUOTA_PER_USER=1):
            for i in range(3):
                response = upload(auth_client, article_for_images, caption=f"第{i}张")
                assert response.status_code == 201, response.json()

        assert GeneratedImage.objects.filter(
            source=GeneratedImage.Source.UPLOAD
        ).count() == 3

    def test_upload_then_generate_still_allowed(self, auth_client, article_for_images, fake_image_provider):
        """额度还有效：先传 2 张实拍图，再生成 AI 图仍然应该成功。

        ★ 和上一条是**两个方向**的断言，都要有：
          上一条证明"上传没被算进去"，这一条证明"额度本身还在生效"
          （只测上一条的话，把额度检查整个删掉也能绿）。
        """
        with override_settings(IMAGE_DAILY_QUOTA_PER_USER=1):
            for i in range(2):
                assert upload(auth_client, article_for_images, caption=f"第{i}张").status_code == 201

            generated = auth_client.post(
                "/api/v1/articles/{}/images".format(article_for_images.id),
                {"suggestion": "配一张"},
                format="json",
            )
            assert generated.status_code == 201, generated.json()

            # 第二张 AI 图就该被额度挡住了
            second = auth_client.post(
                "/api/v1/articles/{}/images".format(article_for_images.id),
                {"suggestion": "再配一张"},
                format="json",
            )
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "IMAGE_QUOTA_EXCEEDED"

    def test_upload_counts_toward_article_limit(self, auth_client, article_for_images):
        """★ 单篇上限**照样管**上传的图。

        和每日额度是两回事：那条防烧钱（所以排除上传），
        这条防的是"一篇文章塞几十张图，正文没法读，推送请求体也爆炸"——
        和花不花钱无关，上传的实拍图当然也算正文图。
        """
        with override_settings(IMAGE_MAX_PER_ARTICLE=1):
            assert upload(auth_client, article_for_images).status_code == 201

            second = upload(auth_client, article_for_images)

        assert second.status_code == 400
        assert second.json()["error"]["code"] == "IMAGE_LIMIT_EXCEEDED"


# ===========================================================================
# 越权
# ===========================================================================
class TestUploadPermissions:
    def test_cannot_upload_to_someone_elses_article(
        self, other_auth_client, article_for_images
    ):
        """★ 别人的文章一律 404 而不是 403 —— 403 等于告诉对方"这个 id 存在"。"""
        response = upload(other_auth_client, article_for_images)

        assert response.status_code == 404
        assert GeneratedImage.objects.count() == 0


# ===========================================================================
# 上传的图必须能走通后面那条老路
# ===========================================================================
class TestUploadedPhotoFlowsThrough:
    """★ 这个功能的意义全在这一组：传上来的照片要能真的发到公众号上。

    不验这一组的话，"上传成功"可能只是一条躺在库里、哪儿也去不了的记录。
    """

    def test_appears_in_copy_html(self, auth_client, article_for_images, settings):
        """上传的图要出现在「复制到公众号」的正文里，用的是绝对地址。

        ★ 绝对地址是硬要求：相对路径粘到 mp.weixin.qq.com 上会被解析成
          mp.weixin.qq.com/media/...，图必裂，而正文文字完全正常 ——
          这种错误很容易漏看。
        """
        settings.MEDIA_PUBLIC_BASE_URL = "http://203.0.113.10:8080"
        image = GeneratedImage.objects.get(
            id=upload(auth_client, article_for_images, caption="现场").json()["data"]["id"]
        )

        response = auth_client.get(COPY_HTML_URL.format(article_id=article_for_images.id))

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["image_count"] == 1
        assert f'src="http://203.0.113.10:8080{image.image_file.url}"' in data["html"]
        assert "现场" in data["html"]

    def test_list_returns_uploads_next_to_generated(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """两种来源的图在列表里要能分辨（前端靠 source 显示"实拍"标记）。"""
        from apps.imaging.services import generate_image
        from apps.articles.models import Article

        article = Article.objects.get(id=article_for_images.id)
        generate_image(article=article, user=article.user, suggestion="配一张")
        upload(auth_client, article)

        results = auth_client.get(
            "/api/v1/articles/{}/images".format(article.id)
        ).json()["data"]["results"]

        sources = sorted(item["source"] for item in results)
        assert sources == ["ai", "upload"]
        labels = {item["source"]: item["source_display"] for item in results}
        assert labels["upload"] == "自己上传"
        assert labels["ai"] == "AI 生成"
