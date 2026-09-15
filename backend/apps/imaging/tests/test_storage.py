"""
存储层测试：图片有没有真的落到磁盘上、路径对不对、删记录会不会留垃圾。

★ 这一份是整个改造里最该有的测试。

  BinaryField -> FileField 这次改动有一堆**只有跑起来才会暴露**的坑，
  而且每一个的症状都很有迷惑性（图片生成了但界面说"图片没了"、
  删了记录磁盘上还在、路径可被枚举）。逐条对着下面这些用例看：

      1. 文件名没进 update_fields  -> 磁盘有文件、列表里全空（最隐蔽的一个）
      2. FileField 空值不是 NULL    -> 用 exclude(image_file=None) 查不到任何东西
      3. 删模型不删文件（Django 行为）-> media/ 无限膨胀
      4. upload_to 用 instance.id    -> 路径可枚举，uuid4 那层保护白做
      5. 扩展名和真实字节不一致      -> 浏览器按扩展名猜 MIME，显示成破图
"""

from __future__ import annotations

import os

import pytest
from django.core.files.base import ContentFile

from apps.imaging.models import GeneratedImage, image_upload_to
from apps.imaging.services import delete_image, generate_image
from apps.imaging.tests.conftest import make_image_bytes

pytestmark = pytest.mark.django_db


class TestImageFileStorage:
    """生成一张图之后，磁盘和数据库两边都要对。"""

    def test_generate_writes_file_to_media_root(
        self, user, article_for_images, fake_image_provider, settings
    ):
        """生成成功时：数据库里有文件名，磁盘上真的有那个文件，字节数对得上。"""
        image = generate_image(
            article=article_for_images, user=user, suggestion="开头配一张总览图"
        )

        assert image.status == GeneratedImage.Status.SUCCEEDED
        assert image.image_file, "文件名没有落库 —— 检查 update_fields 里有没有 image_file"

        path = os.path.join(str(settings.MEDIA_ROOT), image.image_file.name)
        assert os.path.exists(path), f"数据库里有记录但磁盘上没有文件：{path}"
        with open(path, "rb") as fh:
            assert len(fh.read()) == image.byte_size

    def test_file_lands_under_dated_directory(
        self, user, article_for_images, fake_image_provider
    ):
        """路径形如 images/2026/09/<32位十六进制>.jpg。"""
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        parts = image.image_file.name.split("/")

        assert parts[0] == "images"
        assert len(parts[1]) == 4 and parts[1].isdigit(), "年份目录不对"
        assert len(parts[2]) == 2 and parts[2].isdigit(), "月份目录不对"
        # 文件名 = 32 位 uuid4 十六进制 + 扩展名
        assert len(parts[3]) == 36, f"文件名不是 uuid4.hex + ext：{parts[3]}"

    def test_filename_uses_independent_uuid_not_record_id(
        self, user, article_for_images, fake_image_provider
    ):
        """★ 路径里那串 uuid 必须**独立于记录主键**。

        这是整条链路上唯一的访问控制（浏览器的 <img> 带不了 Authorization 头，
        取图只能是公开的，所以"路径猜不出来"就是全部的安全性）。
        一旦有人图省事把 upload_to 改成用 instance.id，
        media/ 目录就变成可枚举的，而且**不会有任何报错、不会有测试变红**。
        所以这条断言必须钉死。
        """
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        filename = image.image_file.name.rsplit("/", 1)[-1].split(".")[0]

        assert filename != image.id.hex
        assert filename != str(image.id).replace("-", "")

    def test_png_gets_png_extension(
        self, user, article_for_images, mocker
    ):
        """PNG 字节 -> .png 扩展名。

        ★ 扩展名和真实字节不一致时，浏览器按扩展名猜 MIME，
          一个 .jpg 里装着 PNG 字节会显示成破图（Chrome 有时能猜回来，
          所以这种 bug 常常只在一部分浏览器上出现）。
        """
        from apps.imaging.tests.conftest import FakeImageProvider

        provider = FakeImageProvider(data=make_image_bytes(fmt="PNG"))
        mocker.patch("apps.imaging.services.get_image_provider", return_value=provider)

        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )

        assert image.content_type == "image/png"
        assert image.image_file.name.endswith(".png")

    def test_unknown_bytes_fall_back_to_jpg_extension(
        self, user, article_for_images, mocker
    ):
        """认不出来的字节 -> 兜底 .jpg，且**不抛异常**。

        Pillow 打不开的字节探不出宽高，但那不该让整条流程崩掉 ——
        宽高只是界面上显示用的。
        """
        from apps.imaging.providers import ImageGenerationResult

        provider = mocker.Mock()
        provider.name = "garbage-provider"
        provider.generate.return_value = ImageGenerationResult(data=b"\x00\x01\x02garbage")
        mocker.patch("apps.imaging.services.get_image_provider", return_value=provider)

        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )

        assert image.image_file.name.endswith(".jpg")
        assert image.width is None
        assert image.height is None

    def test_failed_generation_does_not_write_a_file(
        self, user, article_for_images, mocker, settings
    ):
        """生成失败时**不能**落文件。

        ★ upload_to 每调用一次就生成一个新的 uuid4，
          失败路径如果也去写文件，每失败一次就多一个谁也不知道的孤儿文件。
        """
        from apps.common.exceptions import ImageProviderError
        from apps.imaging.providers import ImageGenerationResult

        provider = mocker.Mock()
        provider.name = "always-fails"
        provider.generate.return_value = ImageGenerationResult(
            error="image_content_rejected", message="提示词被拒绝了。"
        )
        mocker.patch("apps.imaging.services.get_image_provider", return_value=provider)

        with pytest.raises(ImageProviderError):
            generate_image(article=article_for_images, user=user, suggestion="配一张")

        record = GeneratedImage.objects.get(article=article_for_images)
        assert record.status == GeneratedImage.Status.FAILED
        assert not record.image_file
        # 失败记录要留下来（"为什么这张总失败"要有据可查），但磁盘上不该有东西
        media_files = [
            f for _, _, files in os.walk(str(settings.MEDIA_ROOT)) for f in files
        ]
        assert media_files == []


class TestReadBytes:
    """read_bytes()：微信上传那两个地方唯一的取字节入口。"""

    def test_reads_back_the_same_bytes(self, user, article_for_images, fake_image_provider):
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        data = image.read_bytes()

        assert data
        assert len(data) == image.byte_size

    def test_missing_file_returns_empty_bytes_not_exception(
        self, user, article_for_images, fake_image_provider
    ):
        """★ 文件被手工删掉 / 卷没挂上时返回 b""，**不抛异常**。

        这是刻意的：那张图取不到就是"这张图没了"，
        不该让整个推送流程抛 500 让用户看到"服务器内部错误"。
        """
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        image.image_file.delete(save=False)

        assert image.read_bytes() == b""

    def test_no_file_returns_empty_bytes(self, user, article_for_images):
        """压根没有文件的记录（例如失败的那条）也要返回 b""。"""
        record = GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion=""
        )
        assert record.read_bytes() == b""


class TestFileCleanup:
    """删记录要连文件一起删 —— FileField 的经典坑。"""

    def test_delete_image_removes_file_from_disk(
        self, user, article_for_images, fake_image_provider, settings
    ):
        """services.delete_image 之后磁盘上不该留下文件。"""
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        path = os.path.join(str(settings.MEDIA_ROOT), image.image_file.name)
        assert os.path.exists(path)

        delete_image(image=image)

        assert not os.path.exists(path), "删了记录但文件还在，media/ 会无限膨胀"

    def test_cascade_delete_also_removes_file(
        self, user, article_for_images, fake_image_provider, settings
    ):
        """★ 删**文章**（级联删配图）时也要清文件。

        这条路径根本不经过 services.delete_image ——
        admin 里删文章走的就是它。所以清理必须挂在 post_delete 信号上，
        而不是写在 service 函数里。
        """
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        path = os.path.join(str(settings.MEDIA_ROOT), image.image_file.name)
        assert os.path.exists(path)

        article_for_images.delete()

        assert not os.path.exists(path), "级联删除没有清掉图片文件"

    def test_deleting_a_record_without_file_is_safe(self, user, article_for_images):
        """没有文件的记录（失败的那条）删掉时不能报错。

        FileSystemStorage.delete() 自己吞 FileNotFoundError，
        这条用例把"信号里那句 if instance.image_file"钉住 ——
        少了它，空文件名会走到 storage.delete("") 上。
        """
        record = GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion=""
        )
        record.delete()  # 不抛异常就算过


class TestUploadTo:
    """image_upload_to 这个 callable 本身的行为。"""

    def test_rejects_non_image_extension(self):
        """扩展名白名单：.svg / .html 一律换成 .jpg。

        ★ 这是**安全**约束不是格式偏好：/media/ 和网站同源，
          一个 .svg 落进去就是一个存储型 XSS 的入口（SVG 里能写 <script>）。
        """
        instance = GeneratedImage()
        assert image_upload_to(instance, "evil.svg").endswith(".jpg")
        assert image_upload_to(instance, "evil.html").endswith(".jpg")
        assert image_upload_to(instance, "noext").endswith(".jpg")
        # 白名单内的原样保留
        assert image_upload_to(instance, "ok.webp").endswith(".webp")
        assert image_upload_to(instance, "ok.PNG").endswith(".png")

    def test_two_calls_give_different_paths(self):
        """同一个实例调两次必须给两个不同的路径。

        ★ 这条保证了"重新生成必然是新文件名"，
          所以 nginx 那边可以放心给 /media/ 打 immutable 的长缓存 ——
          如果两次会撞名，长缓存就会让用户一直看到旧图，
          而且刷新也没用（缓存是浏览器侧的）。
        """
        instance = GeneratedImage()
        assert image_upload_to(instance, "a.jpg") != image_upload_to(instance, "a.jpg")


class TestHasData:
    """has_data 属性（界面靠它判断要不要显示"图片没了"）。"""

    def test_true_when_file_present(self, user, article_for_images, fake_image_provider):
        image = generate_image(
            article=article_for_images, user=user, suggestion="配一张"
        )
        assert image.has_data is True

    def test_false_when_no_file(self, user, article_for_images):
        record = GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion=""
        )
        assert record.has_data is False
