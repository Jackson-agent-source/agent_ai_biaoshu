"""
build_wechat_html 的纯函数测试。

★ 这个文件里**一条用例都不需要数据库** —— build_wechat_html 是纯函数：
  读几个属性、拼字符串、返回。用 types.SimpleNamespace 造几个假图就够了，
  不用建 Article / GeneratedImage。跑得快，也不会因为模型加字段而批量变红。

★ 为什么现在才补这个文件：它此前是**零覆盖**的，而"复制到公众号"这个功能
  正好要动它（加 src_getter 参数）。动一个零覆盖的函数是危险的 ——
  尤其推送路径的行为必须一字不变地保住，所以第一组用例就是守着它的。

★ 这里守的两条不变量：
    1. 默认 getter 只读 wechat_url，永远不会吐出 /media/ 地址
       （apps/wechat/tests/test_api.py 里也有一条端到端的守着同一个契约）
    2. 换一个 src_getter 之后，wechat_url 为空的图**也能**拼进去 —— 新功能的核心
"""

from __future__ import annotations

from types import SimpleNamespace

from apps.wechat.html_builder import build_wechat_html

# 3 个小节，和 apps/imaging/tests/conftest.py 里那篇文章同一个形状。
# 合法锚点是 0..3，所以测"超界要夹住"时传 9 就行。
MARKDOWN = (
    "## 第一节\n\n这里是第一节的正文。\n\n"
    "## 第二节\n\n这里是第二节的正文。\n\n"
    "## 第三节\n\n这里是第三节的正文。\n"
)

WECHAT_SRC = "https://mmbiz.qpic.cn/mmbiz_jpg/abc.jpg"
SITE_SRC = "http://203.0.113.10:8080/media/images/2026/09/x.jpg"


def make_image(
    *,
    image_id: str = "img-1",
    anchor_index: int = 0,
    sort_order: int = 0,
    wechat_url: str = "",
    caption: str = "",
) -> SimpleNamespace:
    """造一个"长得像 GeneratedImage"的对象。

    刻意不用真的模型：builder 只读这几个属性，用真模型反而要建库、要生图，
    把一个纯函数测试拖成集成测试。
    """
    return SimpleNamespace(
        id=image_id,
        anchor_index=anchor_index,
        sort_order=sort_order,
        wechat_url=wechat_url,
        caption=caption,
    )


def site_getter(_image) -> str:
    """模拟复制路径的 getter：给本站地址。"""
    return SITE_SRC


# ===========================================================================
# 默认 getter —— 推送路径的行为，不许变
# ===========================================================================
class TestDefaultSrcGetter:
    def test_uses_wechat_url(self):
        html = build_wechat_html(
            markdown=MARKDOWN, images=[make_image(anchor_index=1, wechat_url=WECHAT_SRC)]
        )
        assert f'src="{WECHAT_SRC}"' in html
        # 落在第 1 节之后
        assert html.index("这里是第一节的正文") < html.index("<img")

    def test_skips_image_without_wechat_url(self):
        """★ 没有 wechat_url 的图被跳过 —— 这是**现有**行为，不是 bug。

        复制路径上每一张图的 wechat_url 都是空的，所以那条路必须传自己的
        src_getter，不能指望这个默认值。
        """
        html = build_wechat_html(markdown=MARKDOWN, images=[make_image(anchor_index=1)])

        assert "<img" not in html
        # 图没了，文字还在
        assert "这里是第一节的正文" in html

    def test_never_emits_media_path(self):
        """★★ 默认 getter 永远不吐 /media/ 地址。

        微信不允许正文图片外链，发一个 /media/... 过去，浏览器预览里可能看着
        正常，但手机上**就是看不到图**。这条断言是推送路径的核心契约，
        test_api.py 里还有一条端到端的版本。
        """
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=1, wechat_url=WECHAT_SRC)],
        )

        assert "/media/" not in html

    def test_missing_wechat_url_attribute_is_treated_as_empty(self):
        """对象上压根没有 wechat_url 属性也不能炸（getattr 有默认值）。"""
        image = SimpleNamespace(id="x", anchor_index=0, sort_order=0, caption="")
        html = build_wechat_html(markdown=MARKDOWN, images=[image])

        assert "<img" not in html


# ===========================================================================
# 自定义 getter —— 复制到公众号这条路的立身之本
# ===========================================================================
class TestCustomSrcGetter:
    def test_custom_getter_lets_empty_wechat_url_images_through(self):
        """★★ 新功能的核心：换一个 getter，wechat_url 为空的图也能拼进去。

        复制路径上一次微信接口都不调，wechat_url 全是空的 ——
        用默认 getter 会得到一篇**没有图的 HTML**。
        """
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=2, wechat_url="")],
            src_getter=site_getter,
        )

        assert f'src="{SITE_SRC}"' in html
        # 落在第 2 节之后、第 3 节之前
        assert html.index("这里是第二节的正文") < html.index("<img")
        assert html.index("<img") < html.index("这里是第三节的正文")

    def test_custom_getter_returning_none_does_not_crash(self):
        """★ getter 返回 None 时走 `or ""`，图被跳过但不抛异常。

        自定义 getter 是外部传进来的，不能假设它一定返回 str。
        """
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=1)],
            src_getter=lambda _image: None,
        )

        assert "<img" not in html
        assert "这里是第一节的正文" in html

    def test_custom_getter_is_called_once_per_image(self):
        """每张图只调一次 getter —— 调两次的话（比如先探空再取值）纯属浪费。"""
        calls: list[str] = []

        def counting_getter(image) -> str:
            calls.append(image.id)
            return SITE_SRC

        build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(image_id="a", anchor_index=0), make_image(image_id="b", anchor_index=1)],
            src_getter=counting_getter,
        )

        assert calls == ["a", "b"]

    def test_getter_is_ignored_for_empty_image_list(self):
        html = build_wechat_html(markdown=MARKDOWN, images=[], src_getter=site_getter)

        assert "<img" not in html
        assert "这里是第一节的正文" in html


# ===========================================================================
# 锚点：放哪里
# ===========================================================================
class TestAnchorPlacement:
    def test_anchor_beyond_last_section_is_clamped_not_dropped(self):
        """★ 正文被改短之后，原本的锚点可能已经不存在。

        这时**不能丢图** —— 静默丢一张用户已经生成好的配图是非常糟糕的行为，
        夹到最后一节之后，用户能在预览里看到并调整。
        """
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=9, wechat_url=WECHAT_SRC)],
            src_getter=site_getter,
        )

        assert "<img" in html
        # 夹到最后一节（第 3 节）之后
        assert html.index("这里是第三节的正文") < html.index("<img")

    def test_negative_anchor_is_clamped_to_zero(self):
        """anchor_index 字段是**有符号**的，负数要夹到 0（开头），不能当索引越界炸掉。"""
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=-5, wechat_url=WECHAT_SRC)],
            src_getter=site_getter,
        )

        assert "<img" in html
        # 在第一个小节之前（也就是开头段之后）
        assert html.index("<img") < html.index("第一节")

    def test_images_at_same_anchor_are_sorted_by_sort_order(self):
        """同一位置多张图按 sort_order 排。

        数据库返回顺序不保证，不排的话每次推送图片顺序可能不一样。
        """
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[
                make_image(image_id="second", anchor_index=1, sort_order=5, wechat_url="https://mmbiz.qpic.cn/b.jpg"),
                make_image(image_id="first", anchor_index=1, sort_order=1, wechat_url="https://mmbiz.qpic.cn/a.jpg"),
            ],
        )

        assert html.index("https://mmbiz.qpic.cn/a.jpg") < html.index("https://mmbiz.qpic.cn/b.jpg")

    def test_all_images_go_to_index_zero_when_no_sections(self):
        """正文里一个 `## ` 都没有时，所有图都进 index 0（开头段），一张都不能丢。"""
        html = build_wechat_html(
            markdown="只有一段正文，没有任何小标题。",
            images=[
                make_image(image_id="a", anchor_index=0, sort_order=0, wechat_url="https://mmbiz.qpic.cn/a.jpg"),
                make_image(image_id="b", anchor_index=3, sort_order=1, wechat_url="https://mmbiz.qpic.cn/b.jpg"),
            ],
        )

        assert "https://mmbiz.qpic.cn/a.jpg" in html
        assert "https://mmbiz.qpic.cn/b.jpg" in html

    def test_heading_inside_fenced_code_block_is_not_a_section(self):
        """★ 代码块里的 `## ` 不算小标题。

        不处理围栏的话，一段示例代码里的 `## 注释` 会把文章拦腰截断，
        而且是静默发生的 —— 用户只会觉得"图怎么跑到中间去了"。
        """
        markdown = "开头。\n\n```\n## 不是标题\n```\n\n## 真标题\n\n正文。\n"

        html = build_wechat_html(
            markdown=markdown,
            images=[make_image(anchor_index=1, wechat_url=WECHAT_SRC)],
            src_getter=site_getter,
        )

        # 只有一个真小节，所以 anchor_index=1 应该落在「真标题」之后。
        # 如果围栏里那行被当成了小节，这张图会跑到「真标题」**前面** —— 正是这条断言要抓的。
        assert "不是标题" in html
        assert html.index("<img") > html.index("真标题")


# ===========================================================================
# 转义 / 边界
# ===========================================================================
class TestEscapingAndEdgeCases:
    def test_caption_html_is_escaped(self):
        """图注里的 HTML 必须被转义，不能变成真标签。"""
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[
                make_image(
                    anchor_index=1,
                    wechat_url=WECHAT_SRC,
                    caption="<script>alert(1)</script>",
                )
            ],
        )

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_quotes_in_src_cannot_break_out_of_the_attribute(self):
        """★ src 里的引号被转义，没法逃出 src="..." 去注入一个 onerror。"""
        html = build_wechat_html(
            markdown=MARKDOWN,
            images=[make_image(anchor_index=1, wechat_url='http://evil/"onerror="alert(1)')],
        )

        assert 'src="http://evil/&quot;onerror=&quot;alert(1)"' in html
        # 没有裸引号提前闭合属性 —— 上面那条断言同时排除了这种情况
        assert 'onerror="alert(1)"' not in html

    def test_empty_markdown_and_no_images_returns_empty_string(self):
        """空输入返回空串，不是 "<p></p>" 这种占位垃圾。

        前端拿它判空（往剪贴板里写空串会把用户原来复制的东西抹掉）。
        """
        assert build_wechat_html(markdown="", images=[]) == ""
        assert build_wechat_html(markdown="   ", images=[]) == ""

    def test_does_not_mutate_input_images(self):
        """builder 不许改动传进来的对象（它只该读）。

        真要改了，调用方拿到的记录会莫名其妙地变（比如 anchor_index 被夹过），
        然后落库时把用户的原始设置覆盖掉。
        """
        image = make_image(anchor_index=99, sort_order=3, wechat_url=WECHAT_SRC, caption="原样")

        build_wechat_html(markdown=MARKDOWN, images=[image], src_getter=site_getter)

        assert image.anchor_index == 99
        assert image.sort_order == 3
        assert image.wechat_url == WECHAT_SRC
        assert image.caption == "原样"

    def test_headings_are_downgraded_to_paragraphs(self):
        """`h2` 降级成 `p` —— 公众号不接受标题标签，留着会被它删得七零八落。"""
        html = build_wechat_html(markdown=MARKDOWN, images=[])

        assert "<h2" not in html
        assert "<p" in html
        assert "第一节" in html

    def test_section_headings_are_actually_rendered(self):
        """★★ 回归测试：`## ` 小标题必须真的出现在 HTML 里。

        split_markdown_sections 曾经用 `current_lines = []` 开新段，把标题那一行
        整个丢掉 —— 而 build_wechat_html 唯一的输入就是 section.markdown，
        于是**推到公众号的正文里所有 ## 小标题都不见了**：一篇分了三节的文章
        粘过去是一整片没有分节的段落，而且这个 bug 完全无声。

        上面那条 `test_headings_are_downgraded_to_paragraphs` 抓不住它 ——
        "第一节" 正好是"这里是第一节的正文。"的子串，图没了它也照样通过。
        所以这条用**只在大标题里出现**的字串来断言。
        """
        markdown = "引言。\n\n## 独一无二的小标题\n\n正文。\n"

        html = build_wechat_html(markdown=markdown, images=[])

        assert "独一无二的小标题" in html
        # 降级成 p，但样式是标题那一档（字号比正文大、上面留白更多）
        assert "font-size:19px" in html
