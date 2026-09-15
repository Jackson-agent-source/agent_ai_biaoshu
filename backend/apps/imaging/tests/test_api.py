"""
配图接口测试。

    GET    /api/v1/articles/<id>/images
    POST   /api/v1/articles/<id>/images
    PATCH  /api/v1/images/<id>
    DELETE /api/v1/images/<id>
    GET    /api/v1/images/<id>/file      （兜底取图，AllowAny）

重点和 apps/agent/tests/test_api.py 一样：**边界和越权**，不是把 happy path
重复测一遍。另外单独盯住三类最容易静默出错的东西：

    · 序列化器**绝不能**漏出 image_file（磁盘布局）和图片本体
    · 额度/上限必须真的拦得住（配置写错一个字，就是无限烧钱）
    · 越权一律 404 而不是 403（403 等于告诉对方"这个 id 存在"）
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.imaging.models import GeneratedImage
from apps.imaging.services import generate_image

# ★ 用模块级常量硬编码 URL，和 apps/agent/tests/test_api.py 一致。
#   用 reverse() 的话，路由改了测试会跟着一起改 —— 那样就测不出"路由被改坏了"。
ARTICLE_IMAGES_URL = "/api/v1/articles/{article_id}/images"
IMAGE_DETAIL_URL = "/api/v1/images/{image_id}"
IMAGE_FILE_URL = "/api/v1/images/{image_id}/file"

pytestmark = pytest.mark.django_db


def images_url(article) -> str:
    return ARTICLE_IMAGES_URL.format(article_id=article.id)


def detail_url(image) -> str:
    return IMAGE_DETAIL_URL.format(image_id=image.id)


def file_url(image) -> str:
    return IMAGE_FILE_URL.format(image_id=image.id)


# ===========================================================================
# GET 列表
# ===========================================================================
class TestListArticleImages:
    def test_requires_login(self, api_client, article_for_images):
        assert api_client.get(images_url(article_for_images)).status_code == 401

    def test_returns_empty_list_and_section_count(self, auth_client, article_for_images):
        """空列表也要带上 section_count —— 前端要先拿它渲染位置下拉框。"""
        response = auth_client.get(images_url(article_for_images))

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["results"] == []
        assert data["section_count"] == 3

    def test_returns_file_url_after_generation(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """生成之后列表里要有可直接塞进 <img src> 的 file_url。"""
        generate_image(article=article_for_images, user=user, suggestion="配一张")

        data = auth_client.get(images_url(article_for_images)).json()["data"]
        item = data["results"][0]

        assert item["status"] == "succeeded"
        assert item["file_url"].startswith("/media/")
        assert item["file_url"].endswith(".jpg")
        # 宽高来自 Pillow 探测（假的 provider 吐的是真的 40x30 JPEG）
        assert (item["width"], item["height"]) == (40, 30)

    def test_never_exposes_storage_path_or_bytes(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """★ 接口里**不能**出现 image_file 这个字段。

        序列化器故意只暴露派生的 file_url。一旦有人图省事把 image_file 加进
        Meta.fields，前端就会拿到存储层的字段（将来换存储后端、比如上 OSS，
        会变成一个破坏性变更）。图片本体更不能进 JSON ——
        那是当初 BinaryField 时代的老问题，一个列表接口能把响应体撑到几十 MB。

        ★ 注意这里查的是**字段名**，不是路径片段：
          file_url 本身就是由存储路径派生的（/media/images/2026/09/xxx.jpg），
          断言"响应里不能出现 images/2026"是自相矛盾的。
        """
        generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = auth_client.get(images_url(article_for_images))
        raw = response.content.decode("utf-8")

        assert '"image_file"' not in raw
        # 元数据是 KB 级，图片是几百 KB —— 量级不对就说明本体混进来了
        # （用长度判而不是找关键字：base64 里不会出现可认的关键字）
        assert len(raw) < 20_000
        # 该给的还是得给
        assert response.json()["data"]["results"][0]["file_url"].startswith("/media/")

    def test_cross_user_returns_404(self, other_auth_client, article_for_images):
        """★ 越权返回 404 而不是 403 —— 403 会告诉对方"这个 id 是存在的"。"""
        response = other_auth_client.get(images_url(article_for_images))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


# ===========================================================================
# POST 生成
# ===========================================================================
class TestGenerateImageAPI:
    def test_requires_login(self, api_client, article_for_images):
        response = api_client.post(images_url(article_for_images), {}, format="json")
        assert response.status_code == 401

    def test_success_returns_201_and_metadata(
        self, auth_client, article_for_images, fake_image_provider
    ):
        response = auth_client.post(
            images_url(article_for_images),
            {"suggestion": "开头配一张总览图", "suggestion_index": 0, "caption": "总览"},
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["status"] == "succeeded"
        assert data["caption"] == "总览"
        assert data["file_url"].startswith("/media/")
        assert data["byte_size"] > 0
        # 落库了才算真的成功（只有响应里对、库里没有，等于刷新就没了）
        assert GeneratedImage.objects.filter(id=data["id"]).exists()

    def test_style_prompt_is_prepended_to_suggestion(
        self, auth_client, article_for_images, fake_image_provider, settings
    ):
        """★ 发给生图模型的是 IMAGE_STYLE_PROMPT + 画面描述。

        直接把用户看到的"配图建议"丢给模型是不行的 —— 那句话是写给**人**看的
        （"第二节配一张流程图"），不是写给画图模型的。
        """
        auth_client.post(
            images_url(article_for_images),
            {"suggestion": "一只在敲键盘的猫"},
            format="json",
        )

        assert len(fake_image_provider.prompts) == 1
        prompt = fake_image_provider.prompts[0]
        assert settings.IMAGE_STYLE_PROMPT.strip() in prompt
        assert "一只在敲键盘的猫" in prompt

    def test_custom_prompt_overrides_style(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """传了 prompt 就用 prompt —— 用户手写提示词时不该再被画风前缀干扰。"""
        auth_client.post(
            images_url(article_for_images),
            {"suggestion": "被忽略的建议", "prompt": "只发这一句"},
            format="json",
        )

        assert fake_image_provider.prompts == ["只发这一句"]

    def test_anchor_index_at_boundary_is_allowed(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """3 个小节 -> 合法位置是 0..3，边界值 3 必须放行。"""
        response = auth_client.post(
            images_url(article_for_images),
            {"suggestion": "配一张", "anchor_index": 3},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["data"]["anchor_index"] == 3

    def test_anchor_index_out_of_range_is_rejected(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """★ 位置超出小节数要**明确报错**，不能默默存下来。

        anchor_index 比小节数大时，图片在渲染阶段无处可插，会静默消失 ——
        用户看到的是"生成了但推上去没有"，比直接报错难查得多。
        """
        response = auth_client.post(
            images_url(article_for_images),
            {"suggestion": "配一张", "anchor_index": 4},
            format="json",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "超出范围" in body["error"]["message"]
        # 被拦下就不该真的去调生图服务（那是要花钱的）
        assert fake_image_provider.prompts == []
        assert not GeneratedImage.objects.exists()

    def test_empty_prompt_and_suggestion_reports_clearly(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """提示词为空时给一句能action的中文，而不是 500。

        ★ 必须同时把 IMAGE_STYLE_PROMPT 也清空才会走到这个分支：
          build_prompt() 拼的是「画风前缀 + 画面描述」，只要画风还配着，
          空的建议也能拼出一句非空的提示词（于是会照常生图）。
          这不是 bug —— 但它意味着"用户没传 suggestion"时烧掉一张图，
          所以测试里要把这个前提写清楚，否则后来人会以为这条分支死了。
        """
        with override_settings(IMAGE_STYLE_PROMPT=""):
            response = auth_client.post(images_url(article_for_images), {}, format="json")

        assert response.status_code == 502
        body = response.json()
        assert body["error"]["code"] == "IMAGE_PROVIDER_ERROR"
        assert "提示词" in body["error"]["message"]
        # 提示词都没拼出来，就不该去调生图服务（那是要花钱的）
        assert fake_image_provider.prompts == []

    def test_style_prompt_alone_is_enough_to_generate(
        self, auth_client, article_for_images, fake_image_provider, settings
    ):
        """配了画风时，空建议照样会生图 —— 上面的前提在这里被钉住。

        拼出来的是「画风 + \\n\\n画面内容：（空）」，所以只断言前缀，
        不去钉死那句拼接格式 —— 格式改了不该让这条用例变红。
        """
        auth_client.post(images_url(article_for_images), {}, format="json")

        assert len(fake_image_provider.prompts) == 1
        assert fake_image_provider.prompts[0].startswith(
            settings.IMAGE_STYLE_PROMPT.strip()
        )

    def test_provider_error_message_reaches_user(
        self, auth_client, article_for_images, mocker
    ):
        """★ 生图服务的失败原因（key 错/余额不足/被审核）必须原样透给用户。

        这一类错误全都是**用户自己能去改**的，糊成一句"生成失败"等于让他来问你。
        """
        from apps.imaging.providers import ImageGenerationResult

        provider = mocker.Mock()
        provider.name = "quota-exhausted"
        provider.generate.return_value = ImageGenerationResult(
            error="image_provider_quota_exceeded",
            message="生图服务的余额或额度不足，请去中转站后台充值后重试。",
        )
        mocker.patch("apps.imaging.services.get_image_provider", return_value=provider)

        response = auth_client.post(
            images_url(article_for_images), {"suggestion": "配一张"}, format="json"
        )

        assert response.status_code == 502
        assert "充值" in response.json()["error"]["message"]
        # 失败也要留痕，排查"为什么这张总失败"时有据可查
        record = GeneratedImage.objects.get()
        assert record.status == GeneratedImage.Status.FAILED
        assert record.error_message

    def test_not_configured_reports_which_env_var_is_missing(
        self, auth_client, article_for_images, mocker
    ):
        """★ 没配生图服务时要告诉用户**缺哪一项**。

        这是一种部署当天必然遇到的错误（.env 少填一行），
        如果只回一句"生成失败"，用户只能去翻服务器日志。
        """
        from apps.imaging.providers import NullImageProvider

        mocker.patch(
            "apps.imaging.services.get_image_provider",
            return_value=NullImageProvider(),
        )

        response = auth_client.post(
            images_url(article_for_images), {"suggestion": "配一张"}, format="json"
        )

        assert response.status_code == 502
        assert "IMAGE_API_KEY" in response.json()["error"]["message"]

    def test_cross_user_returns_404(self, other_auth_client, article_for_images):
        response = other_auth_client.post(
            images_url(article_for_images), {"suggestion": "配一张"}, format="json"
        )
        assert response.status_code == 404


# ===========================================================================
# 额度与上限
# ===========================================================================
class TestQuotaAndLimit:
    """★ 这三条是"防烧钱"的账本，配置写错一个字就是无限烧钱。"""

    def test_daily_per_user_quota_returns_429(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """额度用完时返回 429 + 中文提示，并且**不再调生图服务**。"""
        with override_settings(IMAGE_DAILY_QUOTA_PER_USER=1):
            first = auth_client.post(
                images_url(article_for_images), {"suggestion": "第一张"}, format="json"
            )
            assert first.status_code == 201

            calls_before = len(fake_image_provider.prompts)
            second = auth_client.post(
                images_url(article_for_images), {"suggestion": "第二张"}, format="json"
            )

        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "IMAGE_QUOTA_EXCEEDED"
        assert "上限" in body["error"]["message"]
        # ★ 关键：额度检查必须在调外部服务**之前**。
        #   放在后面就变成"既花了钱、又报错"，那是双重损失。
        assert len(fake_image_provider.prompts) == calls_before
        assert GeneratedImage.objects.count() == 1

    def test_global_quota_returns_429(
        self, auth_client, other_auth_client, user, other_user, article_for_images,
        fake_image_provider,
    ):
        """全站额度和个人额度是两本账：别人用掉的也算进全站。

        这条挡的是"注册一堆小号把额度摊开"的刷法 ——
        只看个人额度的话，多注册几个账号就绕过去了。
        """
        from apps.articles.models import Article

        other_article = Article.objects.create(
            user=other_user,
            title="别人的文章",
            content_markdown="## 一\n\n正文。",
            source_ids=[],
            risk_notes=[],
            status=Article.Status.DRAFT,
            version=1,
        )
        # 别人先生成一张（个人额度不限，全站额度算上它）
        generate_image(article=other_article, user=other_user, suggestion="别人的图")

        with override_settings(IMAGE_DAILY_QUOTA_GLOBAL=1):
            response = auth_client.post(
                images_url(article_for_images), {"suggestion": "我的图"}, format="json"
            )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "IMAGE_QUOTA_EXCEEDED"
        assert "全站" in response.json()["error"]["message"]

    def test_per_article_limit_returns_400(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """★ 单篇上限给 400 不是 429：用户删一张就能继续，不需要等明天。

        报 429 会让他以为"今天不能再生成图了"，然后就不用了。
        """
        with override_settings(IMAGE_MAX_PER_ARTICLE=1):
            first = auth_client.post(
                images_url(article_for_images), {"suggestion": "第一张"}, format="json"
            )
            assert first.status_code == 201

            second = auth_client.post(
                images_url(article_for_images), {"suggestion": "第二张"}, format="json"
            )

        assert second.status_code == 400
        body = second.json()
        assert body["error"]["code"] == "IMAGE_LIMIT_EXCEEDED"
        assert "单篇上限" in body["error"]["message"]

    def test_quota_zero_means_unlimited(
        self, auth_client, article_for_images, fake_image_provider
    ):
        """0 = 不限制（本地开发的默认值）。别把 0 当成"一张都不给"。"""
        with override_settings(IMAGE_DAILY_QUOTA_PER_USER=0, IMAGE_DAILY_QUOTA_GLOBAL=0):
            for i in range(3):
                response = auth_client.post(
                    images_url(article_for_images), {"suggestion": f"第{i}张"}, format="json"
                )
                assert response.status_code == 201, response.json()


# ===========================================================================
# PATCH / DELETE
# ===========================================================================
class TestUpdateImageAPI:
    def test_updates_caption_and_anchor(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = auth_client.patch(
            detail_url(image),
            {"caption": "新的图注", "anchor_index": 2},
            format="json",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["caption"] == "新的图注"
        assert data["anchor_index"] == 2
        image.refresh_from_db()
        assert image.caption == "新的图注"

    def test_setting_cover_unsets_the_others(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """★ 一篇文章只能有一张封面。

        两张都是 is_cover=True 的时候，微信那边"封面是哪张"就变成了
        数据库返回顺序决定的 —— 用户会看到"我明明设了封面 B"。
        """
        first = generate_image(article=article_for_images, user=user, suggestion="第一张")
        second = generate_image(article=article_for_images, user=user, suggestion="第二张")

        response = auth_client.patch(detail_url(second), {"is_cover": True}, format="json")

        assert response.status_code == 200
        assert response.json()["data"]["is_cover"] is True
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.is_cover is False
        assert second.is_cover is True

    def test_empty_body_is_rejected(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """PATCH 不带任何字段 -> 400，而不是一个"成功但什么都没变"。"""
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = auth_client.patch(detail_url(image), {}, format="json")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_anchor_index_upper_bound_is_enforced(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """序列化器的 max_value 是最后一道 —— 不靠前端下拉框自觉。"""
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = auth_client.patch(detail_url(image), {"anchor_index": 51}, format="json")

        assert response.status_code == 400

    def test_readonly_fields_cannot_be_changed(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        """★ 白名单机制：改不了 status / article / prompt 这些字段。

        EDITABLE_FIELDS 是**白名单**不是黑名单 —— 黑名单的话，
        将来给模型加一个字段就自动变成"可被用户改"，那是很危险的默认值。
        """
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")
        original_prompt = image.prompt

        auth_client.patch(
            detail_url(image),
            {"status": "failed", "prompt": "被篡改的提示词", "caption": "正常图注"},
            format="json",
        )

        image.refresh_from_db()
        assert image.status == GeneratedImage.Status.SUCCEEDED
        assert image.prompt == original_prompt
        assert image.caption == "正常图注"

    def test_cross_user_returns_404(
        self, other_auth_client, user, article_for_images, fake_image_provider
    ):
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = other_auth_client.patch(
            detail_url(image), {"caption": "偷改"}, format="json"
        )

        assert response.status_code == 404
        image.refresh_from_db()
        assert image.caption == ""

    def test_delete_then_404(
        self, auth_client, user, article_for_images, fake_image_provider
    ):
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        assert auth_client.delete(detail_url(image)).status_code == 200
        assert not GeneratedImage.objects.filter(id=image.id).exists()
        assert auth_client.get(detail_url(image)).status_code == 405  # 只有 PATCH/DELETE

    def test_cross_user_delete_returns_404(
        self, other_auth_client, user, article_for_images, fake_image_provider
    ):
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        assert other_auth_client.delete(detail_url(image)).status_code == 404
        assert GeneratedImage.objects.filter(id=image.id).exists()


# ===========================================================================
# 兜底取图接口
# ===========================================================================
class TestImageFileFallbackView:
    """正常路径是 /media/（nginx 直发），这一条只在排查时用。"""

    def test_anonymous_access_is_allowed(
        self, api_client, user, article_for_images, fake_image_provider
    ):
        """★ 必须是 AllowAny。

        浏览器的 <img src> **不会带 Authorization 头**（浏览器行为，改不了），
        所以这个接口没法用 Token 鉴权。安全靠的是 id 是 uuid4 ——
        不是自增 id，也不是会泄露时间和 MAC 的 uuid1。
        """
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")

        response = api_client.get(file_url(image))

        assert response.status_code == 200
        assert response["Content-Type"].startswith("image/")
        # 不被搜索引擎收录
        assert response["X-Robots-Tag"] == "noindex, noimageindex"
        assert b"".join(response.streaming_content)

    def test_unknown_id_returns_404(self, api_client):
        response = api_client.get(
            IMAGE_FILE_URL.format(image_id="00000000-0000-4000-8000-000000000000")
        )
        assert response.status_code == 404

    def test_record_without_file_returns_404(self, api_client, user, article_for_images):
        """失败的那条记录没有文件 -> 404。

        ★ 这里查的是 exclude(image_file="")，不是 exclude(image_file=None)：
          FileField 的空值是**空字符串**不是 NULL，写成 None 会一条都查不出来，
          而且不报错 —— 结果是所有图都 404。
        """
        record = GeneratedImage.objects.create(
            article=article_for_images, user=user, suggestion=""
        )

        response = api_client.get(file_url(record))

        assert response.status_code == 404

    def test_missing_file_on_disk_returns_404_not_500(
        self, api_client, user, article_for_images, fake_image_provider
    ):
        """库里有记录、磁盘上文件没了（卷丢了）-> 404，不是 500。

        500 会让用户以为服务器坏了；404 对调用方来说就是"这张图取不到"，
        和"这张图不存在"是同一个意思。
        """
        image = generate_image(article=article_for_images, user=user, suggestion="配一张")
        image.image_file.delete(save=False)  # 只删文件，不动数据库记录

        assert api_client.get(file_url(image)).status_code == 404
