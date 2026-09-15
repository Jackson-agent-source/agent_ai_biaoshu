"""
公众号推送接口测试。

    GET  /api/v1/articles/<id>/wechat-publishes    推送历史（分页）
    POST /api/v1/articles/<id>/wechat-publishes    推送到草稿箱
    GET  /api/v1/wechat-publishes/<id>             单条详情（含正文快照）

★★ 这里做的永远是**建草稿**，不是发布。★★

   已认证订阅号在 API 层面就拿不到发表权限，所以"推送成功"的断言
   只到 media_id 为止。任何一条用例都不该出现"已发布"这种预期 ——
   后端根本没有那个能力，写了就是自欺欺人。

三条最容易静默出错的边界，各自单独测：

    1. 没配公众号 / 没配图 —— 必须是 400 + 中文，不能是 502 或 500
       （报 502 用户会以为服务器坏了，然后跑去查服务器）
    2. 再推一次是**更新**同一份草稿，不是新建一份
       （不然草稿箱里会攒出一堆几乎一样的草稿，用户在手机上翻半天）
    3. 列表接口**不能**带 content_html
       （整篇 HTML 快照，一页 10 条就是 MB 级响应体）
"""

from __future__ import annotations

import pytest
from django.conf import settings as dj_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import ScopedRateThrottle

from apps.imaging.services import generate_image
from apps.wechat.models import WeChatPublish
from apps.wechat.tests.conftest import FakeWeChatClient
from apps.wechat.views import ArticleWeChatPublishView

# ★ 模块级常量硬编码 URL，路由被改坏时测试才会红（用 reverse() 会跟着一起改）
PUBLISHES_URL = "/api/v1/articles/{article_id}/wechat-publishes"
PUBLISH_DETAIL_URL = "/api/v1/wechat-publishes/{publish_id}"

pytestmark = pytest.mark.django_db


def publishes_url(article) -> str:
    return PUBLISHES_URL.format(article_id=article.id)


def detail_url(record) -> str:
    return PUBLISH_DETAIL_URL.format(publish_id=record.id)


@pytest.fixture
def article_with_image(user, article_for_images, fake_image_provider):
    """一篇**有图**的文章 —— 推送的前置条件。

    返回 (article, image)。生图用的是假 provider，不花钱、不联网。
    """
    image = generate_image(
        article=article_for_images, user=user, suggestion="开头配一张总览图", caption="总览"
    )
    return article_for_images, image


@pytest.fixture
def article_with_two_images(user, article_for_images, fake_image_provider):
    first = generate_image(article=article_for_images, user=user, suggestion="第一张")
    second = generate_image(article=article_for_images, user=user, suggestion="第二张")
    return article_for_images, first, second


# ===========================================================================
# 前置条件：没配置 / 没配图
# ===========================================================================
class TestPreconditions:
    def test_not_configured_returns_400_and_names_the_missing_var(
        self, auth_client, article_with_image
    ):
        """★ 没配公众号 -> 400 + "缺哪一项"。

        这里**故意不用** fake_wechat_client：settings_test 把
        WECHAT_APP_ID / WECHAT_APP_SECRET 留空，走的就是真实的
        get_wechat_client() 抛异常那条路。

        为什么是 400 不是 502：缺配置是**用户自己去 .env 补两行**就能解决的事。
        502 会让他以为服务器坏了，然后去查服务器 —— 方向完全错了。
        """
        article, _ = article_with_image

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "WECHAT_NOT_CONFIGURED"
        assert "WECHAT_APP_ID" in body["error"]["message"]
        # 配置错误不该留下一条 FAILED 记录 —— 那只会让"上次推送"显示一个假故障
        assert WeChatPublish.objects.count() == 0

    def test_no_images_returns_400_with_actionable_hint(
        self, auth_client, fake_wechat_client, article_for_images
    ):
        """★ 没配图 -> 400 + 告诉用户"去生成配图"，而且**一次微信接口都不调**。

        微信 draft/add 的 thumb_media_id 是必填的，没有封面图根本推不出去。
        与其让它到微信那边失败、返回一个看不懂的 errcode，
        不如在这里就说清楚该做什么。
        """
        response = auth_client.post(publishes_url(article_for_images), {}, format="json")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "WECHAT_PRECONDITION_FAILED"
        assert "还没有配图" in body["error"]["message"]
        # 前置检查必须在最前面：白跑一次上传既慢又浪费微信的调用量
        assert fake_wechat_client.body_uploads == []
        assert fake_wechat_client.drafts_added == []
        assert WeChatPublish.objects.count() == 0

    def test_failed_image_does_not_satisfy_precondition(
        self, auth_client, fake_wechat_client, user, article_for_images
    ):
        """★ 只有**生成成功**的图才算数。

        挑图时过滤的是 status=SUCCEEDED。少了这个过滤，一张失败的空记录
        也能让前置检查通过，然后在读字节那一步变成"封面图上传失败"，
        用户完全看不出真正的原因是"图根本没生成出来"。
        """
        from apps.imaging.models import GeneratedImage

        GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion="失败的图",
            status=GeneratedImage.Status.FAILED, error_message="被审核拒绝",
        )

        response = auth_client.post(publishes_url(article_for_images), {}, format="json")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "WECHAT_PRECONDITION_FAILED"


# ===========================================================================
# 推送成功
# ===========================================================================
class TestPublishSuccess:
    def test_creates_draft_and_returns_201(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        article, image = article_with_image

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "succeeded"
        assert data["action"] == "add"
        assert data["media_id"] == "media-id-new"
        assert data["image_count"] == 1
        # 详情接口（也就是这个 POST 的返回）要带正文快照，排查排版问题靠它
        assert data["content_html"]

        assert len(fake_wechat_client.drafts_added) == 1

    def test_draft_payload_uses_wechat_image_url_not_media_path(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★★ 正文里的 <img src> 必须是 mmbiz.qpic.cn，**不能**是 /media/。

        微信不允许正文图片外链。发一个 /media/... 过去，在浏览器预览里
        可能看着正常，但手机上**就是看不到图** —— 这是最容易误判的一类 bug。
        """
        article, image = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")

        # ★ 必须 refresh：服务层操作的是它自己查出来的那个实例，
        #   这里手里的对象不会跟着变（Django 不会同步别的实例的属性）
        image.refresh_from_db()
        payload = fake_wechat_client.last_draft
        assert image.wechat_url.startswith("https://mmbiz.qpic.cn/")
        assert image.wechat_url in payload["content"]
        assert "/media/" not in payload["content"]
        # 封面走 thumb_media_id（必填），不放在正文里
        assert payload["thumb_media_id"] == "thumb-media-id"
        # 图注贴在图片下方（公众号里 alt 不会显示）
        assert "总览" in payload["content"]

    def test_draft_payload_fields(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """给微信的字段名是微信规定的，写错一个就是 400 或静默丢失。"""
        article, _ = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")

        payload = fake_wechat_client.last_draft
        assert payload["title"] == article.title
        assert payload["digest"] == article.summary
        # ★ content_source_url 留空是刻意的：填错会指向一个不相干的页面，
        #   比空着更糟。要填就由用户在公众号后台自己填。
        assert payload["content_source_url"] == ""

    def test_thumb_is_compressed_jpeg(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★ 封面只收 JPG 且约 64KB —— 直接把生图的 PNG 传上去必然失败。

        所以这里断言的是"送到微信手里的字节"已经过了 make_wechat_thumb：
        是 JPEG 文件头，而且不超过 64KB。
        """
        article, _ = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")

        assert len(fake_wechat_client.thumb_uploads) == 1
        _, thumb_bytes = fake_wechat_client.thumb_uploads[0]
        assert thumb_bytes[:3] == b"\xff\xd8\xff", "封面不是 JPEG"
        assert len(thumb_bytes) <= 64 * 1024

    def test_body_image_is_uploaded_once_and_reused(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★ 图片上传过一次就存下 mmbiz 地址复用。

        同一篇文章反复推（改一版推一次）时，第二次推送应该几乎瞬间完成 ——
        没有这个缓存，每次推送都要重传 N 张几百 KB 的图，几十秒起步。
        而且 wechat_url 是按**图片记录**存的，所以只要没重新生成就一直有效。
        """
        article, image = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")
        assert len(fake_wechat_client.body_uploads) == 1

        image.refresh_from_db()
        assert image.wechat_url

        auth_client.post(publishes_url(article), {}, format="json")

        # 第二次一次上传都没有（正文图和封面图的缓存策略不同：
        # 封面每次都要重传，因为 thumb_media_id 是跟着草稿走的）
        assert len(fake_wechat_client.body_uploads) == 1

    def test_second_publish_updates_the_same_draft(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★★ 再推一次是**更新**同一份草稿，不是新建一份。

        用户的心智是"这篇文章在草稿箱里有一份，我改一版就更新它"。
        每次都 add 的话，草稿箱里会堆出一串几乎一样的草稿，
        他在手机上翻半天才能找到最新那份。
        """
        article, _ = article_with_image

        first = auth_client.post(publishes_url(article), {}, format="json").json()["data"]
        second = auth_client.post(publishes_url(article), {}, format="json").json()["data"]

        assert first["action"] == "add"
        assert second["action"] == "update"
        assert second["media_id"] == first["media_id"]
        # 只 add 过一次，更新走的是 update_draft
        assert len(fake_wechat_client.drafts_added) == 1
        assert fake_wechat_client.drafts_updated[0][0] == first["media_id"]
        # 两条记录都要留在历史里（"上次推的是哪一份"要查得到）
        assert WeChatPublish.objects.count() == 2

    def test_force_new_creates_a_second_draft(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """force_new=True 时跳过更新、强制新建。

        ★ 这是接口里就有的能力，但**前端不放这个开关** ——
          40007（草稿被删了）的自动回退已经覆盖了它唯一真实的用途。
          留着是为了将来"确实想同时留两版"时不用改后端。
        """
        article, _ = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")
        response = auth_client.post(
            publishes_url(article), {"force_new": True}, format="json"
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["action"] == "add"
        assert data["media_id"] != "media-id-new"  # 是新建的那一份
        assert len(fake_wechat_client.drafts_added) == 2
        assert fake_wechat_client.drafts_updated == []

    def test_stale_media_id_falls_back_to_add(
        self, auth_client, fake_wechat_client, user, article_with_image
    ):
        """★ 要更新的草稿已经被删了（40007）-> 自动退回新建。

        用户的意图是"推一份草稿过去"，不是"必须更新那一份"。
        因为一份已经不存在的草稿而整体失败，对他毫无意义。
        """
        article, _ = article_with_image
        # 上一次推送成功过，但那份草稿已经被人在后台删了
        WeChatPublish.objects.create(
            article=article, user=user, status=WeChatPublish.Status.SUCCEEDED,
            action=WeChatPublish.Action.ADD, media_id="deleted-media-id",
        )
        fake_wechat_client.update_result = warning_invalid_media_id()

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["action"] == "add"
        assert data["media_id"] == "media-id-new"
        # 先试了更新，失败后才新建 —— 顺序也要对
        assert fake_wechat_client.drafts_updated[0][0] == "deleted-media-id"
        assert len(fake_wechat_client.drafts_added) == 1

    def test_records_content_html_snapshot(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★ 存的是**当时真正发出去的那份 HTML**。

        用户说"排版乱了"的时候，重新用现在的文章渲染一遍是没用的 ——
        文章可能已经被改过了。所以快照必须落库。
        """
        article, _ = article_with_image

        auth_client.post(publishes_url(article), {}, format="json")

        record = WeChatPublish.objects.get()
        assert record.content_html
        assert record.content_html == fake_wechat_client.last_draft["content"]


# ===========================================================================
# 失败路径
# ===========================================================================
class TestPublishFailures:
    def test_wechat_error_keeps_its_chinese_message(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★★ 微信给的中文说明必须原样透给用户。

        40164（IP 白名单）、48001（没权限）这类错误全都是用户去后台
        点两下就能解决的。糊成一句"推送失败"等于让他来问你，
        而你在服务器上只能看到一个 errcode。
        """
        from apps.wechat.client import WeChatResult

        article, _ = article_with_image
        fake_wechat_client.add_result = WeChatResult.failure(
            "wechat_ip_not_whitelisted",
            "服务器的 IP 不在公众号的白名单里。请到 公众号后台 → 设置与开发 → "
            "基本配置 → IP 白名单，把服务器的公网 IP 加进去（最长 10 分钟生效）。",
            errcode=40164,
        )

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 502
        body = response.json()
        assert body["error"]["code"] == "WECHAT_PUBLISH_ERROR"
        assert "IP 白名单" in body["error"]["message"]
        # ★ 失败也要留痕，而且机器码要存下来 —— 界面上"上次推送为什么失败"
        #   就是靠它显示的，不然用户只能看到一个红点
        record = WeChatPublish.objects.get()
        assert record.status == WeChatPublish.Status.FAILED
        assert record.error_code == "wechat_ip_not_whitelisted"
        assert record.media_id == ""

    def test_base_image_upload_failure_is_reported(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """正文图上传失败 -> 明确的失败，绝不"跳过这张继续推"。"""
        from apps.wechat.client import WeChatResult

        article, _ = article_with_image
        fake_wechat_client.body_image_result = WeChatResult.failure(
            "wechat_image_too_large", "图片太大了，微信拒收。"
        )

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 502
        assert "上传失败" in response.json()["error"]["message"]
        # 一张图都没传上去，就不该去建草稿（会建出一份没有图的草稿）
        assert fake_wechat_client.drafts_added == []
        # ★ 记录必须是 FAILED，**不能停在 RUNNING**。
        #   这几条上传失败的分支是直接抛异常的，不补一次落库的话，
        #   前端"上次推送"会一直显示「推送中」，用户以为它还在跑。
        record = WeChatPublish.objects.get()
        assert record.status == WeChatPublish.Status.FAILED
        assert record.error_code == "WECHAT_PUBLISH_ERROR"
        assert record.error_message  # 有中文原因，不是空白

    def test_cover_upload_failure_is_reported(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★ 封面失败必须是硬失败。

        没有 thumb_media_id 就建不了草稿，所以封面这一步没有"降级继续"这个选项。
        """
        from apps.wechat.client import WeChatResult

        article, _ = article_with_image
        fake_wechat_client.thumb_result = WeChatResult.failure(
            "wechat_api_unauthorized", "这个公众号没有调用该接口的权限。"
        )

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 502
        assert "封面图上传失败" in response.json()["error"]["message"]
        assert fake_wechat_client.drafts_added == []
        # 同上：不能停在 RUNNING
        assert WeChatPublish.objects.get().status == WeChatPublish.Status.FAILED

    def test_unexpected_exception_is_recorded_not_just_500(
        self, auth_client, fake_wechat_client, article_with_image, mocker
    ):
        """★ 没预料到的异常也要留痕。

        只抛出去的话，用户看到"服务器内部错误"，而库里**什么记录都没有** ——
        "上次推送"显示空白，完全无从查起。所以 except Exception 那一支
        必须先把 record 落成 FAILED 再抛。
        """
        article, _ = article_with_image
        mocker.patch.object(
            fake_wechat_client, "add_draft", side_effect=RuntimeError("boom")
        )

        response = auth_client.post(publishes_url(article), {}, format="json")

        assert response.status_code == 502
        record = WeChatPublish.objects.get()
        assert record.status == WeChatPublish.Status.FAILED
        assert record.error_code == "unexpected_error"
        # 内部异常信息可以留在库里给开发者看，但不能原样给用户
        assert "boom" not in response.json()["error"]["message"]


# ===========================================================================
# 封面选择
# ===========================================================================
class TestCoverSelection:
    """get_cover_image 的优先级：显式指定 -> is_cover -> 第一张成功的图。

    放在服务层测，因为它不依赖任何 HTTP 细节，而"选错封面"是
    用户唯一会肉眼发现的问题（推上去的封面不是他挑的那张）。
    """

    def test_explicit_id_wins(self, user, article_with_two_images):
        from apps.imaging.services import get_cover_image

        article, first, second = article_with_two_images
        first.is_cover = True
        first.save(update_fields=["is_cover"])

        assert get_cover_image(article, preferred_id=second.id).id == second.id

    def test_is_cover_wins_when_no_explicit_id(self, user, article_with_two_images):
        from apps.imaging.services import get_cover_image

        article, first, second = article_with_two_images
        second.is_cover = True
        second.save(update_fields=["is_cover"])

        assert get_cover_image(article).id == second.id

    def test_falls_back_to_the_first_image(self, user, article_with_two_images):
        """★ 这个兜底很重要：用户可能直接点"推送"而从来没设过封面。

        这时候不该报错让他回去点一下 —— 拿第一张就好。
        """
        from apps.imaging.services import get_cover_image

        article, first, _ = article_with_two_images

        assert get_cover_image(article).id == first.id

    def test_failed_image_is_never_chosen(self, user, article_for_images, fake_image_provider):
        """只有 SUCCEEDED 的图能当封面（失败的那条没有文件，选了也传不上去）。"""
        from apps.imaging.models import GeneratedImage
        from apps.imaging.services import generate_image as gen, get_cover_image

        ok = gen(article=article_for_images, user=user, suggestion="成功的")
        GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion="失败的",
            status=GeneratedImage.Status.FAILED,
        )

        assert get_cover_image(article_for_images).id == ok.id


# ===========================================================================
# 封面归属校验
# ===========================================================================
class TestCoverImageValidation:
    def test_another_articles_image_is_rejected(
        self, auth_client, fake_wechat_client, user, article_for_images, fake_image_provider
    ):
        """★ 传了别的文章的图片 id -> 400，而不是静默用默认封面。

        服务层的 queryset 本来就按 article 过滤过，传别人的 id 只会
        **静默回退**到默认封面 —— 用户以为选了封面 B，实际推上去的是封面 A，
        而他要在手机上打开草稿箱才会发现。
        """
        from apps.articles.models import Article

        other_article = Article.objects.create(
            user=user, title="另一篇文章", content_markdown="## 一\n\n正文。",
            source_ids=[], risk_notes=[], status=Article.Status.DRAFT, version=1,
        )
        foreign = generate_image(article=other_article, user=user, suggestion="别人的图")
        generate_image(article=article_for_images, user=user, suggestion="自己的图")

        response = auth_client.post(
            publishes_url(article_for_images),
            {"cover_image_id": str(foreign.id)},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert fake_wechat_client.drafts_added == []

    def test_failed_image_cannot_be_used_as_cover(
        self, auth_client, fake_wechat_client, user, article_for_images, fake_image_provider
    ):
        """显式指定的封面也必须是**生成成功**的那张。"""
        from apps.imaging.models import GeneratedImage

        failed = GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion="失败的",
            status=GeneratedImage.Status.FAILED,
        )
        generate_image(article=article_for_images, user=user, suggestion="成功的")

        response = auth_client.post(
            publishes_url(article_for_images),
            {"cover_image_id": str(failed.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "封面图" in response.json()["error"]["message"]


# ===========================================================================
# 推送历史
# ===========================================================================
class TestPublishHistory:
    def test_list_excludes_content_html(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """★★ 列表**不能**带 content_html。

        它是整篇 HTML 快照，几十到几百 KB。一页 10 条一起带上，
        响应体会直接涨到 MB 级 —— 这和当初"把图片二进制放进列表接口"
        是同一个错误，只是换了个字段。
        """
        article, _ = article_with_image
        auth_client.post(publishes_url(article), {}, format="json")

        response = auth_client.get(publishes_url(article))

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["count"] == 1
        assert "content_html" not in data["results"][0]
        assert "content_html" not in response.content.decode("utf-8")

    def test_detail_includes_content_html(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        """要看快照就走详情接口 —— 排查排版问题只有这一个入口。"""
        article, _ = article_with_image
        created = auth_client.post(publishes_url(article), {}, format="json").json()["data"]

        response = auth_client.get(PUBLISH_DETAIL_URL.format(publish_id=created["id"]))

        assert response.status_code == 200
        assert response.json()["data"]["content_html"]

    def test_list_is_paginated_and_newest_first(
        self, auth_client, fake_wechat_client, article_with_image
    ):
        article, _ = article_with_image
        auth_client.post(publishes_url(article), {}, format="json")
        auth_client.post(publishes_url(article), {}, format="json")

        data = auth_client.get(publishes_url(article)).json()["data"]

        assert data["count"] == 2
        assert data["page"] == 1
        assert data["results"][0]["action"] == "update"  # 最新的在前
        assert data["results"][1]["action"] == "add"

    def test_requires_login(self, api_client, article_for_images):
        assert api_client.get(publishes_url(article_for_images)).status_code == 401

    def test_cross_user_list_returns_404(self, other_auth_client, article_with_image):
        article, _ = article_with_image
        assert other_auth_client.get(publishes_url(article)).status_code == 404

    def test_cross_user_detail_returns_404(
        self, auth_client, other_auth_client, fake_wechat_client, article_with_image
    ):
        """单条详情按 user 过滤 —— 推送记录里有正文快照，更不能越权看到。"""
        article, _ = article_with_image
        created = auth_client.post(publishes_url(article), {}, format="json").json()["data"]

        response = other_auth_client.get(
            PUBLISH_DETAIL_URL.format(publish_id=created["id"])
        )

        assert response.status_code == 404


# ===========================================================================
# 限流
# ===========================================================================
class TestThrottleWiring:
    """★ 限流最容易**静默失效**：没写 throttle_scope 就等于不限流。

    ScopedRateThrottle 把"没有 scope"当成"不限制"，表现是一切正常，
    直到有人把微信的调用量刷光（微信有 45009 上限）。
    所以这里不测"打满额度"，而是直接钉住配置本身。
    """

    def test_scope_is_configured(self):
        rates = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        scope = getattr(ArticleWeChatPublishView, "throttle_scope", None)

        assert scope == "wechat_publish", "view 上的 throttle_scope 丢了，等于不限流"
        assert scope in rates, f"scope {scope!r} 没有对应的速率配置"

    def test_get_is_not_throttled_but_post_is(self):
        """★ throttle_scope 是**类级**属性，所以必须按方法分发。

        直接用类属性的话，GET（查推送历史）也会被限流 ——
        用户在详情页多刷几次就吃 429，而他什么都没干。
        """
        factory = APIRequestFactory()
        view = ArticleWeChatPublishView()

        view.request = view.initialize_request(factory.get("/x"))
        assert view.get_throttles() == [], "查推送历史不该被限流"

        view.request = view.initialize_request(factory.post("/x"))
        throttles = view.get_throttles()
        assert len(throttles) == 1
        assert isinstance(throttles[0], ScopedRateThrottle)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def warning_invalid_media_id():
    """微信对"草稿不存在"的回应（errcode 40007）。

    单独抽出来是因为它带着 40164/40125 那套**用户向的中文说明** ——
    服务层要靠 result.error（机器码）才能认出该退回新建，
    靠 message 去匹配字符串是绝对不行的。
    """
    from apps.wechat.client import WeChatResult, translate_error

    error, message = translate_error(40007, "invalid media_id")
    return WeChatResult.failure(error, message, errcode=40007)
