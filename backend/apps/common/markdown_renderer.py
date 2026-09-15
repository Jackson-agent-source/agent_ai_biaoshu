"""
Markdown -> 安全 HTML。

Agent 返回的是 body_markdown，但微信公众号后台 / 网页展示需要 HTML。
这里做两件事：
1. 用 Python-Markdown 把 Markdown 渲染成 HTML；
2. 用 nh3 清洗结果，去掉 <script>、onclick 等一切可执行内容，防止 XSS。

安全提醒：前端拿到 content_html 后仍然建议再经 DOMPurify 处理一次（双保险）。
"""

from __future__ import annotations

import logging

import markdown as markdown_lib
import nh3

logger = logging.getLogger(__name__)

# 允许出现在文章里的 HTML 标签。Markdown 渲染出来的常用标签都在这里。
ALLOWED_TAGS: set[str] = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "del", "mark", "sub", "sup",
    "ul", "ol", "li",
    "blockquote",
    "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img",
    "span", "div",
}

# 允许的属性。注意：没有 onclick / onerror 这类事件属性。
#
# ★ 这里**故意不允许 rel**：nh3 底层的 ammonia 规定，一旦设置了 link_rel，
#   就不能再手工允许 rel 属性，否则会直接 panic（Rust 层面的 assert 失败，
#   抛出的 PanicException 继承自 BaseException，连 except Exception 都抓不住，
#   会直接把整个请求打挂）。rel 由下面的 link_rel 参数统一注入，效果一样。
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class"},   # 代码高亮用
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
    "th": {"align"},
    "td": {"align"},
}

ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}

_MD_EXTENSIONS = [
    "extra",        # 表格、删除线、围栏代码块等常用扩展
    "sane_lists",   # 更符合直觉的列表解析
    "nl2br",        # 单个换行也当作 <br>，符合公众号排版习惯
]


def render_markdown_to_html(markdown_text: str) -> str:
    """把 Markdown 转成清洗过的安全 HTML。

    出错时返回一个包含错误提示的 <p>，不让整个接口挂掉。
    """
    if not markdown_text or not markdown_text.strip():
        return ""

    try:
        raw_html = markdown_lib.markdown(markdown_text, extensions=_MD_EXTENSIONS, output_format="html")
    except Exception:
        logger.exception("Markdown 渲染失败")
        return "<p>（Markdown 渲染失败，请查看原始 Markdown 内容）</p>"

    try:
        return nh3.clean(
            raw_html,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            url_schemes=ALLOWED_URL_SCHEMES,
            link_rel="noopener noreferrer",  # 外链自动加 rel，防 tabnabbing
        )
    except Exception:
        logger.exception("HTML 清洗失败")
        return "<p>（HTML 清洗失败，请查看原始 Markdown 内容）</p>"
