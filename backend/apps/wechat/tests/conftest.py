"""
公众号推送测试的 fixture。

★ 假客户端的粒度和 apps/imaging/tests 的假 Provider 不一样，这里要说清楚：

    真实链路（前置检查 → 上传正文图 → 上传封面 → 拼 HTML → 建/更新草稿 → 落库）
    全部照跑，只把**唯一一个要联网的东西**换掉：WeChatClient。

    但和生图不同的是，微信这几个方法的"返回什么"直接决定了服务层的分支：

        upload_body_image 成功 -> 图片记录里写上 wechat_url（下次推送跳过上传）
        upload_thumb      失败 -> 整个推送失败
        update_draft 报 40007 -> **退回 add_draft** 新建一份
        add_draft      报错   -> 落一条 FAILED 记录并把中文原因抛给用户

    所以假客户端不是"永远成功"，而是每个方法的返回值都可以单独注入 ——
    这些分支才是真正会出问题的地方，happy path 反而没什么好测的。
"""

from __future__ import annotations

import pytest

# ★ 复用 apps/imaging/tests 的两个 fixture，而不是在这里再抄一份。
#   原因：pytest 的 conftest 是**按目录生效**的，apps/imaging/tests/conftest.py
#   里的 fixture 在 apps/wechat/tests/ 下根本看不见。而"推送到公众号"这件事
#   天然依赖"文章得有配图"，两边的前置状态必须完全一样（3 个 ## 小节、
#   真的 JPEG 字节），抄一份迟早会漂移。
#   fixture 就是普通函数，import 进 conftest 的命名空间就等于在这里定义了。
from apps.imaging.tests.conftest import (  # noqa: F401
    article_for_images,
    fake_image_provider,
    make_image_bytes,
)
from apps.wechat.client import WeChatResult


class FakeWeChatClient:
    """假的微信客户端。

    默认全部成功；想让某一步失败就传入对应的 *_result。
    """

    def __init__(
        self,
        *,
        media_id: str = "media-id-new",
        add_result: WeChatResult | None = None,
        update_result: WeChatResult | None = None,
        body_image_result: WeChatResult | None = None,
        thumb_result: WeChatResult | None = None,
    ) -> None:
        self.media_id = media_id
        self.add_result = add_result
        self.update_result = update_result
        self.body_image_result = body_image_result
        self.thumb_result = thumb_result

        # 记下每一次调用，供断言使用
        self.body_uploads: list[tuple[str, bytes]] = []
        self.thumb_uploads: list[tuple[str, bytes]] = []
        self.drafts_added: list[dict] = []
        self.drafts_updated: list[tuple[str, dict]] = []

        # add_draft 每次返回一个新的 media_id 更有说服力（能区分"新建了一份"
        # 和"复用了上一次那份"）。第一次是 media_id，之后依次加后缀。
        self._add_count = 0

    # ---- 上传 ----
    def upload_body_image(self, data: bytes, filename: str = "image.jpg") -> WeChatResult:
        self.body_uploads.append((filename, data))
        if self.body_image_result is not None:
            return self.body_image_result
        return WeChatResult(ok=True, url=f"https://mmbiz.qpic.cn/mmbiz_jpg/{filename}")

    def upload_thumb(self, data: bytes, filename: str = "cover.jpg") -> WeChatResult:
        self.thumb_uploads.append((filename, data))
        if self.thumb_result is not None:
            return self.thumb_result
        return WeChatResult(ok=True, media_id="thumb-media-id")

    # ---- 草稿 ----
    def add_draft(self, article: dict) -> WeChatResult:
        self._add_count += 1
        self.drafts_added.append(article)
        if self.add_result is not None:
            return self.add_result
        suffix = "" if self._add_count == 1 else f"-{self._add_count}"
        return WeChatResult(ok=True, media_id=f"{self.media_id}{suffix}")

    def update_draft(self, media_id: str, article: dict, index: int = 0) -> WeChatResult:
        self.drafts_updated.append((media_id, article))
        if self.update_result is not None:
            return self.update_result
        return WeChatResult(ok=True)

    # ---- 断言辅助 ----
    @property
    def last_draft(self) -> dict:
        """最后一次提交给微信的草稿内容（新建或更新都算）。"""
        if self.drafts_updated:
            return self.drafts_updated[-1][1]
        return self.drafts_added[-1]


@pytest.fixture
def fake_wechat_client(mocker):
    """把微信客户端换成假的，并返回它。

    ★ 必须 patch apps.wechat.services 里的那个名字，不能 patch
      apps.wechat.client.get_wechat_client ——
      services.py 是 `from ...client import get_wechat_client`，
      名字在 import 时就绑到它自己的命名空间上了，
      patch 原模块对它没有任何影响（这是最常见的 patch 无效原因）。
    """
    client = FakeWeChatClient()
    mocker.patch("apps.wechat.services.get_wechat_client", return_value=client)
    return client
