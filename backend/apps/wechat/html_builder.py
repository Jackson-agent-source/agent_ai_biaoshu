"""
公众号正文 HTML 生成器。

★ 为什么不能直接用 apps/common/markdown_renderer.render_markdown_to_html()：

    那个渲染器是给**浏览器**看的。公众号编辑器是另一套规则，而且非常苛刻：

        · `class` 属性完全无效 —— 公众号会把 <style> 和外部 CSS 全部删掉，
          只认**内联 style**。所以"用 CSS 类控制排版"这条路是死的。
        · `h1`/`h2` 会被降级成 `p`
        · `style` 属性里多一个空格都可能失效（所以下面的样式表全部写成
          单行、冒号后不带多余空格）
        · 正文图片**不允许外链**，src 必须是我们上传后拿到的 mmbiz.qpic.cn 地址

    所以这里的做法是：**先用现有渲染器把 Markdown 变成干净 HTML，
    再由 html_builder 逐标签补上内联样式。**

★ 关于顺序（这是安全性的关键）：

    1. render_markdown_to_html() 内部用 nh3 做白名单消毒（不含 style）
    2. 我们再注入自己的 <img>
    3. 最后 inline_styles() 用 HTMLParser 重写一遍，给白名单标签加 style

    因为消毒在**前**、注入在**后**，所有进到最终 HTML 里的 style 值
    都是本文件里写死的字符串常量，没有任何来自用户或网络的片段。
    换句话说：**没有注入面**。这也是不用 nh3 的 style 白名单功能的原因 ——
    那个要写一长串正则去允许 style，等于把风险引进来。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from html.parser import HTMLParser
from typing import Any

from apps.common.markdown_renderer import render_markdown_to_html
from apps.common.markdown_sections import MarkdownSection, split_markdown_sections

logger = logging.getLogger(__name__)

# 复用同一个切段函数。放在 apps/common 是为了让前端/配图/这里三方共用一条规则，
# 这里再导出一次只是为了让调用方写 import 顺手。
__all__ = [
    "split_markdown_sections",
    "MarkdownSection",
    "build_wechat_html",
    "inline_styles",
    "image_tag",
]


# ---------------------------------------------------------------------------
# 内联样式表
# ---------------------------------------------------------------------------
# ★★ 格式要求（踩过坑，别改）：
#     · 整个值必须是**一行**，不能换行
#     · 冒号后面不加空格
#     · 分号结尾
#   公众号的编辑器对 style 的解析比浏览器严格得多，多余空白会导致整条样式被丢掉。
_TAG_STYLES: dict[str, str] = {
    "p": "margin:0 0 16px;font-size:16px;line-height:1.75;color:#3f3f3f;",
    "blockquote": "margin:0 0 16px;padding:10px 14px;border-left:3px solid #d0d0d0;"
    "color:#666666;background:#f7f7f7;",
    "ul": "margin:0 0 16px;padding-left:22px;",
    "ol": "margin:0 0 16px;padding-left:22px;",
    "li": "margin:0 0 8px;font-size:16px;line-height:1.75;color:#3f3f3f;",
    "img": "max-width:100%;height:auto;display:block;margin:16px auto;",
    "hr": "border:none;border-top:1px solid #e5e5e5;margin:24px 0;",
    "a": "color:#576b95;text-decoration:none;",
    "strong": "font-weight:bold;color:#1a1a1a;",
    "em": "font-style:italic;",
    "pre": "margin:0 0 16px;padding:12px;background:#f6f8fa;border-radius:4px;"
    "overflow-x:auto;font-size:13px;line-height:1.6;",
    "code": "font-family:Menlo,Consolas,monospace;font-size:13px;color:#c7254e;"
    "background:#f7f7f7;padding:1px 4px;border-radius:3px;",
    "table": "width:100%;border-collapse:collapse;margin:0 0 16px;font-size:14px;",
    "th": "border:1px solid #e0e0e0;padding:8px;background:#f7f7f7;text-align:left;",
    "td": "border:1px solid #e0e0e0;padding:8px;",
    "span": "",  # span 不加样式，只保留结构
    "div": "",   # 同上
}

# 标题统一降级成 <p>。
# ★ 为什么不用 h2/h3：微信会把 h1/h2 改成 p。与其让它改（改了之后
#   我们挂在 h1/h2 上的样式可能一起被丢掉），不如自己直接输出带样式的 p，
#   所见即所得。h5/h6 太小，按普通段落处理。
#
# 这些 key 是**原始的 markdown 标题级别**，不是输出标签名。
_HEADING_STYLES: dict[int, str] = {
    1: "margin:0 0 18px;font-size:20px;font-weight:bold;line-height:1.5;color:#1a1a1a;",
    2: "margin:28px 0 16px;font-size:19px;font-weight:bold;line-height:1.5;color:#1a1a1a;",
    3: "margin:22px 0 12px;font-size:17px;font-weight:bold;line-height:1.5;color:#1a1a1a;",
    4: "margin:18px 0 10px;font-size:16px;font-weight:bold;line-height:1.5;color:#1a1a1a;",
    5: "margin:0 0 16px;font-size:16px;line-height:1.75;color:#3f3f3f;",
    6: "margin:0 0 16px;font-size:16px;line-height:1.75;color:#3f3f3f;",
}

_HEADING_RE = re.compile(r"^h([1-6])$")


def _out_tag(tag: str) -> str:
    """这个标签最终会被写成什么标签。标题一律降级成 p。"""
    return "p" if _HEADING_RE.match(tag) else tag


# 自闭合标签，不能输出 </xxx>
_VOID_TAGS = frozenset({"img", "br", "hr", "input", "meta", "link", "source", "col"})

# 这两个标签连同内容一起丢掉。正常路径上 nh3 已经把它们清掉了，
# 这里是第二道 —— 因为 inline_styles() 是个公开函数，
# 将来可能有人拿未经消毒的 HTML 直接调它。
_DROP_TAGS = frozenset({"script", "style", "iframe", "object", "embed"})


# ---------------------------------------------------------------------------
# 图片标签
# ---------------------------------------------------------------------------
def image_tag(src: str, caption: str = "") -> str:
    """拼一个公众号正文能用的 <img>。

    ★ src 必须是 uploadimg 返回的 mmbiz.qpic.cn 地址 —— 微信不让正文外链图片，
      放别的地址在手机上是**看不到图**的（浏览器里可能显示正常，很容易误判）。

    图注用 <p> 贴在图片下方，而不是用 alt/title：
    公众号里 alt 不会显示出来，用户看不到图注就没有意义。
    """
    if not src:
        return ""

    safe_src = html_lib.escape(src, quote=True)
    tags = [f'<img src="{safe_src}" style="{_TAG_STYLES["img"]}">']

    text = (caption or "").strip()
    if text:
        safe_caption = html_lib.escape(text, quote=False)
        tags.append(
            f'<p style="margin:8px 0 20px;font-size:13px;line-height:1.6;'
            f'color:#999999;text-align:center;">{safe_caption}</p>'
        )

    return "".join(tags)


# ---------------------------------------------------------------------------
# 正文拼装
# ---------------------------------------------------------------------------
def build_wechat_html(*, markdown: str, images: list[Any]) -> str:
    """把 Markdown 正文和配图拼成公众号能吃的 HTML。

    参数：
        markdown  文章正文（Article.content_markdown）
        images    配图列表。每一项要有 anchor_index / sort_order / wechat_url / caption

    返回：已经补好内联样式的 HTML 字符串。

    ★ 锚点规则和前端、和配图接口完全一致：按正文里**真实出现的 `## `** 切段，
      anchor_index=0 是开头，N 是第 N 个小节之后。
    """
    sections = split_markdown_sections(markdown or "")
    last_index = len(sections) - 1

    # 按位置分组。排序保证同一位置多张图时顺序稳定
    # （数据库返回顺序不保证，不排的话每次推送图片顺序可能不一样）。
    grouped: dict[int, list[Any]] = {}
    for image in images or []:
        index = getattr(image, "anchor_index", 0) or 0
        if index > last_index:
            # 正文被改短了，原本的位置已经不存在。
            # ★ 不丢图 —— 静默丢一张用户已经生成好的配图是非常糟糕的行为，
            #   放到最后一段之后，用户能在手机预览里看到并调整。
            logger.warning(
                "配图 anchor_index=%s 超出正文小节数（%s），已放到末尾 image_id=%s",
                index,
                last_index,
                getattr(image, "id", "?"),
            )
            index = last_index
        if index < 0:
            index = 0
        grouped.setdefault(index, []).append(image)

    for bucket in grouped.values():
        bucket.sort(key=lambda item: getattr(item, "sort_order", 0) or 0)

    parts: list[str] = []

    for section in sections:
        # 每一段都单独走一遍渲染器 —— 这样标题、列表这些块级元素的
        # 上下文不会跨段粘连（比如一个没闭合的列表吃掉后面一整节）。
        rendered = render_markdown_to_html(section.markdown or "")
        if rendered:
            parts.append(rendered)

        for image in grouped.get(section.index, []):
            src = getattr(image, "wechat_url", "") or ""
            if not src:
                # 到这一步还没上传成功，说明上游漏了上传。
                # 不静默跳过 —— 让用户看到少了一张图，比"推送成功但没图"好。
                logger.error(
                    "配图还没有微信地址，已跳过 image_id=%s", getattr(image, "id", "?")
                )
                continue
            parts.append(image_tag(src, getattr(image, "caption", "")))

    return inline_styles("".join(parts))


# ---------------------------------------------------------------------------
# 内联样式重写
# ---------------------------------------------------------------------------
class _StyleRewriter(HTMLParser):
    """给 HTML 补内联样式，顺便丢掉 class 和一些不该出现的标签。

    用 HTMLParser 而不是正则：
        正则改 HTML 是经典的错误做法 —— 属性里有 ">" 的、注释里的标签、
        文本里的 "<"，都会让正则错位。HTMLParser 是标准库自带的
        流式解析器，虽然 API 老派，但对"标签 + 属性"这种结构足够可靠。
    """

    def __init__(self) -> None:
        # ★ convert_charrefs=False 是必须的。
        #   默认 True 会把 &amp; 这类实体直接解成 & 再交给 handle_data，
        #   我们重新拼回去的时候就无法区分"原本的 &amp;"和"文本里裸的 &"，
        #   会产生不合法的 HTML。False 时实体会单独走 handle_entityref，
        #   我们原样吐回去，一个字符都不动。
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self._drop_tag = ""
        self._drop_depth = 0

    # -- 标签 --
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._drop_depth:
            if tag == self._drop_tag:
                self._drop_depth += 1
            return
        if tag in _DROP_TAGS:
            self._drop_tag = tag
            self._drop_depth = 1
            return
        self.parts.append(self._render_start(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """<img /> 这种自闭合写法走这里。"""
        if self._drop_depth or tag in _DROP_TAGS:
            return
        self.parts.append(self._render_start(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        if self._drop_depth:
            if tag == self._drop_tag:
                self._drop_depth -= 1
            return
        if tag in _DROP_TAGS or tag in _VOID_TAGS:
            return
        # ★ 必须走 _out_tag：h1~h6 在开始标签那里已经被改写成 p 了，
        #   闭合标签如果还吐 </h1>，就会得到 <p>...</h1> 这种错配的 HTML。
        #   公众号对错配标签的容错很差，轻则样式丢失，重则整段内容不显示。
        self.parts.append(f"</{_out_tag(tag)}>")

    # -- 文本 --
    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._drop_depth:
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._drop_depth:
            return
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        """注释一律丢掉。公众号不需要，而且注释里能藏东西。"""

    def handle_decl(self, decl: str) -> None:
        """DOCTYPE 之类，丢掉。"""

    # -- 内部 --
    def _render_start(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        out_tag = _out_tag(tag)

        heading = _HEADING_RE.match(tag)
        if heading:
            # 标题降级成 p，但要保住层级对应的字号
            style = _HEADING_STYLES.get(int(heading.group(1)), _TAG_STYLES["p"])
        else:
            style = _TAG_STYLES.get(tag, "")

        pieces: list[str] = []
        for name, value in attrs:
            # ★ class 直接扔掉。公众号不认 class，留着只会让 HTML 变大，
            #   而且会给人一种"我写了 CSS 生效了"的错觉。
            if name == "class":
                continue
            # style 也扔掉 —— 现在这个 HTML 还没被我们注入过 style
            # （注入发生在本函数里），如果这里出现了 style，
            # 只可能是调用方自己带进来的。为了"所有 style 都是我们自己写的"
            # 这条不变量，一律丢弃。
            if name == "style":
                continue
            if value is None:
                pieces.append(f" {name}")
                continue
            pieces.append(f' {name}="{html_lib.escape(str(value), quote=True)}"')

        if style:
            pieces.append(f' style="{style}"')

        attrs_text = "".join(pieces)
        if out_tag in _VOID_TAGS:
            return f"<{out_tag}{attrs_text}>"
        return f"<{out_tag}{attrs_text}>"

    def result(self) -> str:
        return "".join(self.parts)


def inline_styles(html: str) -> str:
    """给 HTML 里的标签补上内联样式。

    输入应当是**已经消毒过的** HTML（比如 render_markdown_to_html 的输出）。
    即使不是，这里也会丢掉 script/style/iframe 等标签 —— 但那是第二道防线，
    不要把安全性寄托在这个函数上。
    """
    if not html:
        return ""

    parser = _StyleRewriter()
    try:
        parser.feed(html)
        parser.close()
        return parser.result()
    except Exception:
        # HTMLParser 对畸形 HTML 相当宽容，正常不该抛。
        # 真抛了也不能让整个推送挂掉 —— 返回一份**纯文本**，
        # 让用户至少能在草稿箱里看到内容（而不是什么都没有）。
        logger.exception("内联样式重写失败，降级为纯文本")
        return f"<p style=\"{_TAG_STYLES['p']}\">{html_lib.escape(html)}</p>"
