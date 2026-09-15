"""
配图测试共用的 fixture 和辅助函数。

核心思路和 apps/agent/tests/conftest.py 一样：**让测试真实但不花钱**。

    真实的链路（额度检查 → 建记录 → 写文件 → 更新状态 → 序列化）全部照跑，
    只把唯一一个"要花钱、要联网、还慢"的东西换掉：
        生图模型 -> FakeImageProvider（直接吐一张真的 JPEG 字节）

★ 为什么假 provider 要吐**真的**图片字节，而不是 b"fake"：
  services.generate_image 会拿字节去过 image_utils.probe()（读宽高）和
  sniff_content_type()（看文件头）。给一堆假字节的话，宽高永远是 None、
  落盘扩展名永远走兜底 —— 于是"扩展名和真实格式不一致导致浏览器显示破图"
  这类真正会出问题的 bug，测试**照不出来**。
  Pillow 是现成的依赖，造一张 40x30 的真图成本极低。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from apps.imaging.providers import ImageGenerationResult


def make_image_bytes(width: int = 40, height: int = 30, fmt: str = "JPEG") -> bytes:
    """造一张**真的**图片字节。

    ★ 默认很小（40x30）是有意的：测试不需要高分辨率，
      小图编解码快，一套测试能快好几秒。
      fmt 可以传 "PNG" 来测扩展名/MIME 的判断。
    """
    buffer = io.BytesIO()
    # 用纯色而不是随机噪声：噪声图 JPEG 压不小，也没多测到任何东西
    Image.new("RGB", (width, height), (120, 160, 200)).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeImageProvider:
    """假的生图 Provider。

    默认每次都成功。想让某几次失败就传 errors=[...]，
    列表里第 N 个元素对应第 N 次调用（None 表示这次成功）——
    这样"第 2 张失败、第 3 张还能成功"这种行为可以精确地测出来。
    """

    name = "fake-provider"

    def __init__(
        self,
        *,
        data: bytes | None = None,
        errors: list[ImageGenerationResult | None] | None = None,
        revised_prompt: str = "",
    ) -> None:
        self.data = data if data is not None else make_image_bytes()
        self.errors = list(errors or [])
        self.revised_prompt = revised_prompt
        # 记下每一次收到的提示词，用来断言"画风前缀拼进去了"
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> ImageGenerationResult:
        self.prompts.append(prompt)
        index = len(self.prompts) - 1

        if index < len(self.errors) and self.errors[index] is not None:
            return self.errors[index]  # type: ignore[return-value]

        return ImageGenerationResult(
            data=self.data,
            revised_prompt=self.revised_prompt,
        )


@pytest.fixture
def fake_image_provider(mocker):
    """把生图 Provider 换成假的，并返回它（用来断言收到了什么提示词）。

    ★ 必须 patch apps.imaging.services 里的那个名字，不能 patch
      apps.imaging.providers.get_image_provider ——
      services.py 是 `from ... import get_image_provider`，
      名字在 import 时就绑到它自己的命名空间上了，
      patch 原模块对它没有任何影响（这是最常见的 patch 无效原因）。
    """
    provider = FakeImageProvider()
    mocker.patch("apps.imaging.services.get_image_provider", return_value=provider)
    return provider


@pytest.fixture
def article_for_images(user):
    """一篇属于 user 的文章，正文里有 3 个 "## " 小节。

    小节数刻意是 3：插入位置的合法范围是 0..3，
    测"超出范围要报错"时传 4 就行。
    """
    from apps.articles.models import Article

    return Article.objects.create(
        user=user,
        title="配图测试文章",
        subtitle="",
        summary="摘要",
        hook="",
        outline=["一", "二", "三"],
        content_markdown=(
            "## 第一节\n\n这里是第一节的正文。\n\n"
            "## 第二节\n\n这里是第二节的正文。\n\n"
            "## 第三节\n\n这里是第三节的正文。\n"
        ),
        content_html="",
        image_suggestions=["开头配一张总览图", "第二节配一张流程图", "第三节配一张对比图"],
        source_ids=[],
        risk_notes=[],
        status="draft",
        version=1,
    )
