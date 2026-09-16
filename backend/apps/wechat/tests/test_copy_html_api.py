"""
「复制到公众号」接口测试。

    GET /api/v1/articles/<id>/wechat-html

★★ 这条路和推送那条路是**两件独立的事**，测试也要分开写：

    推送  要凭据、要 IP 白名单、要草稿权限、必须有封面图
    复制  什么都不需要，没配凭据时一次微信接口都不碰

    所以下面第一组用例里最重要的一条是"没有配图也是 200" ——
    推送在同样的情况下必须返回 400（见 test_api.py 的 TestPreconditions），
    这不是不一致，是两个接口的前置条件本来就不同。

三条最容易静默出错的地方，各自单独测：

    1. **图片地址必须是绝对的**。相对路径粘到 mp.weixin.qq.com 上会被解析成
       mp.weixin.qq.com/media/...，图必裂，而正文文字完全正常 —— 很容易漏看。
    2. **image_count 必须等于真的拼进去了几张**。地址取不到的图会被摘掉，
       这时候还照着"候选张数"报数，前端就会告诉用户"已复制 3 张配图"，
       用户粘过去一张都没有。
    3. **没配凭据时一次微信接口都不许调**。这是本功能存在的全部意义 ——
       有权限的号用推送就行了，用复制的恰恰是推送走不通的那些。

★ 后来加了一件事：**配了凭据的话，复制前会把配图先传一次微信**
  （services._rehost_images_best_effort）。因为公众号**正文图不允许外链** ——
  编辑器根本不去抓我们站上的图，粘过去就是四个"图片载入失败"的灰框
  （2026-09 实测，四张图全挂）。
  但它是**尽力而为**：没配凭据、接口报错、连续失败主动放弃 ——
  每一步都必须静默退回本站地址，绝不能让复制这条路跟着坏掉。
  文件末尾的 TestRehostImagesBestEffort 就是在钉死这件事。
"""

from __future__ import annotations

import pytest

from apps.imaging.models import GeneratedImage
from apps.imaging.services import generate_image, public_file_url
from apps.wechat.client import WeChatResult

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
# 复制前把配图传一次微信（尽力而为）
# ===========================================================================
@pytest.fixture
def wechat_credentials(settings):
    """配上微信凭据 —— 单测用的假值，长度按真实规格。

    ★ 必须显式配：settings_test.py 刻意把两个值留空，
      好让"没配凭据"那条路径成为默认。这里的用例要测的是配了之后的行为。
    """
    settings.WECHAT_APP_ID = "wx_test_appid_0001"
    settings.WECHAT_APP_SECRET = "0" * 32
    return settings


class TestRehostImagesBestEffort:
    """★★ 这一组的主题只有一个：**上传失败绝不能把复制搞坏**。

    为什么值得单独一组：这个改动把一个原本"零外部依赖"的接口，
    变成了"会去调微信"的接口。风险全在失败路径上 ——
    40164（IP 白名单没配）、48001（个人订阅号没 uploadimg 权限）
    在真实服务器上都是**常态**，而这条路必须在那时候照样能用。
    """

    def test_rehosts_images_when_credentials_work(
        self, auth_client, article_with_image, base_url, wechat_credentials, mocker
    ):
        """★ 配了凭据且上传成功 -> 正文里必须是 mmbiz 地址。

        用 mmbiz 地址才是"不用赌"的那条路：公众号正文图不允许外链，
        编辑器不会去抓我们站上的图。换成 mmbiz 之后必定显示得出来。
        """
        fake_client = mocker.Mock()
        fake_client.get_access_token.return_value = WeChatResult(ok=True, token="t")
        fake_client.upload_body_image.return_value = WeChatResult(
            ok=True, url="https://mmbiz.qpic.cn/mmbiz_jpg/rehosted.jpg"
        )
        mocker.patch("apps.wechat.services.get_wechat_client", return_value=fake_client)

        article, image = article_with_image
        data = get_data(auth_client, article)

        assert "https://mmbiz.qpic.cn/mmbiz_jpg/rehosted.jpg" in data["html"]
        # 本站地址必须**不再**出现 —— 出现了说明退回了老路
        assert f"{base_url}/media/" not in data["html"]
        assert data["image_count"] == 1
        assert data["warning"] == ""

        # 地址写回了记录：下次复制不用再传一遍
        image.refresh_from_db()
        assert image.wechat_url == "https://mmbiz.qpic.cn/mmbiz_jpg/rehosted.jpg"

    def test_still_works_when_client_explodes(
        self, auth_client, article_with_image, base_url, wechat_credentials, mocker
    ):
        """★★ 配了凭据但拿客户端就抛异常 —— 仍然 200，仍然用本站地址。

        这是本次改动最要紧的一条：少让它变成新的前置条件。
        """
        mocker.patch(
            "apps.wechat.services.get_wechat_client",
            side_effect=RuntimeError("微信炸了"),
        )

        article, image = article_with_image
        data = get_data(auth_client, article)

        assert data["image_count"] == 1
        assert f'src="{base_url}{image.image_file.url}"' in data["html"]

        image.refresh_from_db()
        assert image.wechat_url == ""

    def test_falls_back_when_access_token_fails(
        self, auth_client, article_with_image, base_url, wechat_credentials, mocker
    ):
        """★ 拿不到 access_token（40164 IP 白名单没配）-> 退回本站地址。

        40164 是真实部署里最常见的一种失败：代码全对，
        只是服务器的出口 IP 没加进公众号后台的白名单。
        这种时候**不该上传任何一张图** —— 已经知道一定会失败。
        """
        fake_client = mocker.Mock()
        fake_client.get_access_token.return_value = WeChatResult(
            ok=False, errcode=40164, error="wechat_ip_not_in_whitelist"
        )
        mocker.patch("apps.wechat.services.get_wechat_client", return_value=fake_client)

        article, image = article_with_image
        data = get_data(auth_client, article)

        assert data["image_count"] == 1
        assert f'src="{base_url}{image.image_file.url}"' in data["html"]
        # token 都没拿到，一张都不该试
        fake_client.upload_body_image.assert_not_called()

    def test_falls_back_when_uploadimg_is_unauthorized(
        self, auth_client, article_with_image, base_url, wechat_credentials, mocker
    ):
        """★ 个人订阅号没有 uploadimg 权限（48001）-> 退回本站地址。

        这条大概率就是线上真实情况：号是个人订阅号，
        media/uploadimg 这个接口根本没开。那时候复制功能必须照常可用，
        用户手动往编辑器里插图即可。
        """
        fake_client = mocker.Mock()
        fake_client.get_access_token.return_value = WeChatResult(ok=True, token="t")
        fake_client.upload_body_image.return_value = WeChatResult(
            ok=False, errcode=48001, error="wechat_api_unauthorized"
        )
        mocker.patch("apps.wechat.services.get_wechat_client", return_value=fake_client)

        article, image = article_with_image
        data = get_data(auth_client, article)

        assert data["image_count"] == 1
        assert f'src="{base_url}{image.image_file.url}"' in data["html"]

        image.refresh_from_db()
        assert image.wechat_url == ""

    def test_gives_up_after_consecutive_failures(
        self, user, article_for_images, fake_image_provider, auth_client, base_url,
        wechat_credentials, mocker
    ):
        """★ 连续失败 2 张就放弃，不要把剩下的都试一遍。

        一篇文章可能有十几张配图。48001 这种错是**权限级**的 ——
        第一张失败，后面每一张都必然失败。全试完既慢（每次一个网络往返）、
        又会在微信侧刷出一串无谓的失败调用。
        """
        for i in range(4):
            generate_image(
                article=article_for_images, user=user, suggestion=f"第 {i} 张", caption=f"图{i}"
            )

        fake_client = mocker.Mock()
        fake_client.get_access_token.return_value = WeChatResult(ok=True, token="t")
        fake_client.upload_body_image.return_value = WeChatResult(
            ok=False, errcode=48001, error="wechat_api_unauthorized"
        )
        mocker.patch("apps.wechat.services.get_wechat_client", return_value=fake_client)

        data = get_data(auth_client, article_for_images)

        # 4 张图都在，全部退回本站地址 —— 功能没受影响
        assert data["image_count"] == 4
        assert f"{base_url}/media/" in data["html"]
        # 但只试了 2 次（_REHOST_MAX_CONSECUTIVE_FAILURES）
        assert fake_client.upload_body_image.call_count == 2


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
