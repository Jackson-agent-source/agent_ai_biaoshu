"""
按 Markdown 里的二级标题切分正文段落。

★ 为什么这个函数放在 common 而不是某个 app 里：

    "哪一段算第几节"这件事，**前端、配图、公众号渲染器三方必须完全一致**。
    如果各写一份，早晚会出现"预览里图在小标题 A 下面，推到公众号跑到 B 下面"，
    而且这种 bug 只有真机上肉眼看得出来。

    放在 apps/common 是因为它是个**纯文本函数**，不碰任何 Model ——
    放在 apps/wechat 里会让 apps/imaging 反过来依赖 apps/wechat（循环依赖）。

★ 为什么按正文里真实出现的 "## " 切，而不是按 Article.outline 切：

    outline 是**大模型自己填的**结构化输出，它和正文里的实际小标题
    经常对不上（少一个、多一个、顺序不同、措辞不同）。
    用户看到的是正文，所以"第几节"必须以正文为准。
"""

from __future__ import annotations

import re
from typing import NamedTuple

# 只匹配正好两个 # 后面跟空白的情况。
# 因此 "### 三级标题" 不会被匹配（## 后面是 #，不是空白），
# 这正是我们要的 —— 三级标题属于它所在的那个二级小节。
HEADING_RE = re.compile(r"^##\s+(.*)$")

# 围栏代码块：``` 或 ~~~ 开头结尾（允许带语言标注，如 ```python）
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


class MarkdownSection(NamedTuple):
    """一个正文段落。

    index 就是锚点值：
        0 = 开头（第一个二级标题之前）
        N = 第 N 个二级小节的正文之后
    title 为空字符串表示这是开头的引言部分。
    """

    index: int
    title: str
    markdown: str


def split_markdown_sections(markdown: str) -> list[MarkdownSection]:
    """把 markdown 按 "## " 小标题切成若干段。

    返回的列表**至少有一个元素**（即使正文里一个小标题都没有）。
    第一个元素是"开头段"：它包含标题之前的全部内容，title 是空字符串。

    ★ 代码块里的 "## " 不算小标题。
      不处理围栏的话，一段示例代码里的 `## 注释` 会把文章拦腰截断，
      而且是静默发生的 —— 用户只会觉得"图怎么跑到中间去了"。
    """
    text = markdown or ""
    lines = text.split("\n")

    sections: list[MarkdownSection] = []
    current_title = ""
    current_lines: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        sections.append(
            MarkdownSection(
                index=len(sections),
                title=current_title,
                markdown="\n".join(current_lines).strip("\n"),
            )
        )

    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0]  # 只记 ` 还是 ~
            elif marker[0] == fence_marker:
                in_fence = False
                fence_marker = ""
            current_lines.append(line)
            continue

        match = None if in_fence else HEADING_RE.match(line)
        if match:
            # 遇到新的二级标题：先把上一段收尾，再开新的一段
            flush()
            current_title = match.group(1).strip()
            current_lines = []
            continue

        current_lines.append(line)

    flush()
    return sections


def section_count(markdown: str) -> int:
    """正文里有几个二级小节（不含开头的引言段）。"""
    return max(0, len(split_markdown_sections(markdown)) - 1)


def section_titles(markdown: str) -> list[str]:
    """所有二级小标题的文本，按出现顺序。"""
    return [s.title for s in split_markdown_sections(markdown) if s.title]
