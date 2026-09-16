"""
「复制到公众号」接口测试。

    GET /api/v1/articles/<id>/wechat-html

★★ 这条路和推送那条路是**两件独立的事**，测试也要分开写：

    推送  要凭据、要 IP 白名单、要草稿权限、必须有封面图
    复制  什么都不需要，一次微信接口都不碰

    所以下面第一组用例里最重要的一条是"没有配图也是 200" ——
    推送在同样的情况下必须返回 400（见 test_api.py 的 TestPreconditions），
    这不是不一致，是两个接口的前置条件本来就不同。

三条最容易静默出错的地方，各自单独测：

    1. **图片地址必须是绝对的**。相对路径粘到 mp.weixin.qq.com 上会被解析成
       mp.weixin.qq.com/media/...，图必裂，而正文文字完全正常 —— 很容易漏看。
    2. **image_count 必须等于真的拼进去了几张**。地址取不到的图会被摘掉，
       这时候还照着"候选张数"报数，前端就会告诉用户"已复制 3 张配图"，
       用户粘过去一张都没有。
    3. **一次微信接口都不许调**。这是本功能存在的全部意义 ——
       有权限的号用推送就行了，用复制的恰恰是推送走不通的那些。
"""

from __future__ import annotations

import pytest

from apps.imaging.models import GeneratedImage
from apps.imaging.services import generate_image, public_file_url

# ★ 模块级常量硬编码 URL，路由被改坏时测试才会红（用 reverse() 会跟着一起改）
COPY_HTML_URL = "/api/v1/articles/{article_id}/wechat-html"

# 线上是 http://<公网IP>:8080 —— 带端口这件事本身就是要测的点
BASE_URL = "http://203.0.113.10:8080"

pytestmark = pytest.mark.django_db


def copy_url(article) -> str:
    return COPY_HTML_URL.format(article_id=article.id)


@pytest.fixture
def base_url(settings):
    """配上图片对外基地址。

    ★ 必须用 settings fixture 而不是依赖 .env —— 否则这条用例在配过
      MEDIA_PUBLIC_BASE_URL 的机器上绿、在没配的机器上红。
    """
    settings.MEDIA_PUBLIC_BASE_URL = BASE_URL
    return BASE_URL


@pytest.fixture
def article_with_image(user, article_for_images, fake_image_provider):
    """一篇有图的文章。返回 (article, image)。生图走假 provider，不花钱不联网。"""
    image = generate_image(
        article=article_for_images, user=user, suggestion="开头配一张总览图", caption="总览"
    )
    return article_for_images, image


def get_data(client, article) -> dict:
    response = client.get(copy_url(article))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    return body["data"]


# ===========================================================================
# 基本形状
# ===========================================================================
class TestCopyHtmlBasics:
    def test_returns_body_with_absolute_image_url(self, auth_client, article_with_image, base_url):
        article, image = article_with_image

        data = get_data(auth_client, article)

        assert data["image_count"] == 1
        assert data["section_count"] == 3
        # ★ 绝对地址，而且**带端口** —— 相对路径粘到公众号里必裂
        assert f'src="{base_url}{image.image_file.url}"' in data["html"]
        assert "/media/" in data["html"]
        assert data["warning"] == ""

    def test_html_contains_body_text_with_inline_styles(self, auth_client, article_for_images, base_url):
        """正文渲染成带内联样式的 HTML（公众号只认内联 style）。"""
        data = get_data(auth_client, article_for_images)

        assert "这里是第一节的正文。" in data["html"]
        assert "style=" in data["html"]
        # 标题降级成 p，公众号不接受 h2
        assert "<h2" not in data["html"]

    def test_excludes_title_and_summary(self, auth_client, article_for_images, base_url):
        """★ 只复制正文 —— 标题/摘要在公众号编辑器里是两个独立输入框。

        塞进正文里用户还得手动删掉，不如不塞。
        """
        article = article_for_images

        data = get_data(auth_client, article)

        assert article.title not in data["html"]
        assert article.summary not in data["html"]


# ===========================================================================
# 和推送最大的区别：没有配图也是 200
# ===========================================================================
class TestNoPreconditions:
    def test_no_images_returns_200_not_400(self, auth_client, article_for_images, base_url):
        """★★ 没有配图 -> 200 + 完整正文。

        推送在同样的情况下必须 400（微信 draft/add 的 thumb_media_id 是必填的），
        但复制只复制文字是完全正当的用法。把推送那套前置检查搬过来，
        就等于把"我想只发文字"的用户挡在门外。
        """
        data = get_data(auth_client, article_for_images)

        assert data["image_count"] == 0
        assert data["section_count"] == 3
        assert "<img" not in data["html"]
        assert "这里是第一节的正文。" in data["html"]
        # ★ 纯文字复制不需要任何图片地址，不该报一个假警告
        assert data["warning"] == ""

    def test_empty_body_returns_200_with_empty_html(self, auth_client, article_for_images, base_url):
        """空正文 -> 200 + 空 html。

        后端不报错，由前端拦（往剪贴板里写空串会把用户原来复制的东西抹掉，
        所以判空必须在**复制之前**做，见 utils/clipboard.ts）。
        """
        article = article_for_images
        article.content_markdown = ""
        article.save(update_fields=["content_markdown"])

        data = get_data(auth_client, article)

        assert data["html"] == ""
        assert data["section_count"] == 0

    def test_works_without_any_wechat_credentials(self, auth_client, article_with_image, base_url, mocker):
        """★★ 微信 API 完全不可用也能用 —— 这是本功能存在的意义。

        settings_test.py 把 WECHAT_APP_ID / WECHAT_APP_SECRET 留空，
        这里再把 get_wechat_client 打成"一调就炸"：
        只要这条路碰了一次微信接口，测试立刻红。
        """
        mocker.patch(
            "apps.wechat.services.get_wechat_client",
            side_effect=AssertionError("这条路不该碰任何微信接口"),
        )
        article, image = article_with_image
        # 从没推送过 —— wechat_url 是空的，正是"号没有草稿权限"的用户的真实状态
        assert image.wechat_url == ""

        data = get_data(auth_client, article)

        assert data["image_count"] == 1
        assert "<img" in data["html"]


# ===========================================================================
# 图片地址：取哪来的、取不到怎么办
# ===========================================================================
class TestImageUrlResolution:
    def test_prefers_existing_wechat_url_over_site_url(
        self, auth_client, article_with_image, base_url
    ):
        """★ 已经上传到微信的图优先用 mmbiz 地址。

        它在 https 的 mmbiz.qpic.cn 上，粘进公众号编辑器必定显示得出来；
        本站地址则要看编辑器会不会重新上传，是个赌。
        一个 or 就能两全，没有任何理由不这么做。
        """
        article, image = article_with_image
        image.wechat_url = "https://mmbiz.qpic.cn/mmbiz_jpg/abc.jpg"
        image.save(update_fields=["wechat_url", "updated_at"])

        data = get_data(auth_client, article)

        assert "https://mmbiz.qpic.cn/mmbiz_jpg/abc.jpg" in data["html"]
        # 这张图走的是 mmbiz，不该再出现本站地址
        assert f"{base_url}/media/" not in data["html"]

    def test_without_base_url_images_are_skipped_and_warned(
        self, auth_client, article_with_image, settings
    ):
        """★ 没配 MEDIA_PUBLIC_BASE_URL -> 一张图都不放，并明确告诉用户去配。

        宁可少图，也不要一墙裂图：塞一个相对路径过去，
        用户粘到公众号里看到的是一排裂图，比"没有图"更难看、更难解释。
        """
        settings.MEDIA_PUBLIC_BASE_URL = ""
        article, _ = article_with_image

        data = get_data(auth_client, article)

        assert data["image_count"] == 0
        assert "<img" not in data["html"]
        # 文字照常复制
        assert "这里是第一节的正文。" in data["html"]
        # 警告必须点名那个环境变量 —— 否则用户不知道该去配什么
        assert "MEDIA_PUBLIC_BASE_URL" in data["warning"]

    def test_trailing_slash_in_base_url_does_not_double_up(
        self, auth_client, article_with_image, settings
    ):
        """基地址结尾多一个 / 不能拼出 //media/。

        settings.py 里已经 rstrip 过一次，但 settings fixture 是直接赋值的 ——
        两条路都得挡住，因为 /media//x.jpg 在有些 nginx 配置下就是 404。
        """
        settings.MEDIA_PUBLIC_BASE_URL = f"{BASE_URL}/"
        article, image = article_with_image

        data = get_data(auth_client, article)

        assert f"{BASE_URL}{image.image_file.url}" in data["html"]
        assert "//media/" not in data["html"]

    def test_succeeded_image_without_file_is_excluded_and_warned(
        self, auth_client, user, article_for_images, base_url
    ):
        """★ 状态是 succeeded 但文件为空的记录不算数，而且要如实告诉用户。

        真实场景：文件在磁盘上丢了。这时候还报 image_count=1，
        用户粘过去发现少一张，只会以为是复制功能有问题。
        """
        GeneratedImage.objects.create(
            article=article_for_images,
            user=user,
            suggestion="没有文件的图",
            status=GeneratedImage.Status.SUCCEEDED,
        )

        data = get_data(auth_client, article_for_images)

        assert data["image_count"] == 0
        assert "没有图片文件" in data["warning"]

    def test_public_file_url_returns_empty_for_missing_file(self, user, article_for_images):
        """★ image_file 为空时返回空串，而不是抛 ValueError。

        Django 的 FieldFile.url 在没有文件时会抛 ValueError ——
        那是 500，用户看到的是"服务器内部错误"，完全指不到真正的原因。
        """
        image = GeneratedImage.objects.create(
            article=article_for_images,
            user=user,
            suggestion="没有文件的图",
            status=GeneratedImage.Status.SUCCEEDED,
        )

        assert image.image_file == ""  # FileField 的空值是空串不是 NULL
        assert public_file_url(image) == ""


# ===========================================================================
# 图片插在哪里
# ===========================================================================
class TestImagePlacement:
    def test_image_lands_after_its_anchor_section(self, auth_client, user, article_for_images, base_url, fake_image_provider):
        """配图按 anchor_index 插在对应小节之后，不是全堆在开头或末尾。"""
        image = generate_image(
            article=article_for_images,
            user=user,
            suggestion="第二节配一张流程图",
            anchor_index=2,
        )

        data = get_data(auth_client, article_for_images)

        html = data["html"]
        assert image.id is not None
        assert html.index("这里是第二节的正文。") < html.index("<img")
        assert html.index("<img") < html.index("这里是第三节的正文。")

    def test_failed_image_is_not_included(self, auth_client, user, article_for_images, base_url):
        """只有生成成功的图才算数。"""
        GeneratedImage.objects.create(
            article=article_for_images,
            user=user,
            suggestion="失败的图",
            status=GeneratedImage.Status.FAILED,
            error_message="被审核拒绝",
        )

        data = get_data(auth_client, article_for_images)

        assert data["image_count"] == 0
        assert "<img" not in data["html"]

    def test_section_count_matches_images_api(self, auth_client, article_for_images, base_url):
        """section_count 和 /articles/<id>/images 用的是同一条切段规则。

        ★ 两边必须一致，否则前端那个"插入到第几节"的下拉框
          和这里报的小节数会对不上，用户看到的选项比实际能放的位置多。
        """
        copy_data = get_data(auth_client, article_for_images)
        images_response = auth_client.get(f"/api/v1/articles/{article_for_images.id}/images")

        assert images_response.status_code == 200
        assert copy_data["section_count"] == images_response.json()["data"]["section_count"]


# ===========================================================================
# 权限
# ===========================================================================
class TestCopyHtmlPermissions:
    def test_requires_login(self, api_client, article_for_images):
        assert api_client.get(copy_url(article_for_images)).status_code == 401

    def test_cross_user_returns_404(self, other_auth_client, article_for_images, base_url):
        """★ 越权返回 404 而不是 403 —— 403 会告诉对方"这个 id 是存在的"。"""
        response = other_auth_client.get(copy_url(article_for_images))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
