"""
Agent 最终必须返回的结构化结果。

为什么用 Pydantic 而不是让模型自由发挥然后正则解析？
1. 可靠。LangChain create_agent 的 response_format 会把这份 Schema 转成工具定义，
   模型必须按字段填，框架负责校验。不用写脆弱的正则。
2. 字段有 description，模型知道每个字段该写什么、不该写什么。
3. 校验失败时框架会自动让模型重试，而不是我们拿到一坨无法解析的文本。

数据流：
    LLM -> ArticleDraft（本文件）-> Django 校验来源 -> Article 模型 -> MySQL
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """文章引用的一条来源。

    重要约束：url 必须是本次运行中 web_search 搜到过、或 read_webpage 读过的地址。
    如果填了一个系统没见过的 URL，Django 会把它删掉（见 SourceRegistry）。
    """

    title: str = Field(
        description="来源标题，直接使用搜索结果里的 title，不要自己改写或翻译。",
    )
    url: str = Field(
        description=(
            "来源链接。必须是本次对话中 web_search 返回过的、或 read_webpage 读取过的完整 URL。"
            "绝对不要凭记忆或推测填写，也不要编造看起来合理的链接。"
        ),
    )
    usage: str = Field(
        description=(
            "这个来源在文章里具体提供了什么信息，一句话说明。"
            "例如：提供了 2026 年第一季度 AI Agent 市场规模的数据。"
        ),
    )


class ArticleDraft(BaseModel):
    """一篇微信公众号文章的完整草稿。这是 Agent 的最终产出物。"""

    title: str = Field(
        description=(
            "文章标题。要求：具体、有信息量、能引起点击欲望，控制在 30 个汉字以内。"
            "避免『浅谈』『探析』『关于……的思考』这类学术腔和标题党式的夸张。"
        ),
    )

    subtitle: str | None = Field(
        default=None,
        description="副标题（可填 null）。用来补充说明文章角度或范围，一句话即可。",
    )

    summary: str = Field(
        description="文章摘要，100~150 字。用于公众号推送预览，要能独立说清文章讲了什么。",
    )

    hook: str = Field(
        description=(
            "文章的开头段落，2~4 句话。作用是让读者愿意继续读下去。"
            "可以用一个具体场景、一个反常识的事实、或一个真实的问题切入。"
            "不要用『在当今时代』『随着……的不断发展』这类空话开头。"
        ),
    )

    outline: list[str] = Field(
        description=(
            "文章大纲，3~6 条，每条是一个小标题（不要带序号）。"
            "顺序要和 body_markdown 里实际的小标题顺序一致。"
        ),
    )

    body_markdown: str = Field(
        description=(
            "文章正文，使用 Markdown 格式。"
            "用 ## 作为小标题（二级标题），段落之间空一行，"
            "可以用 **加粗** 强调关键结论，可以用 - 列表罗列要点。"
            "不要把整篇文章写成一大段；重要数据要说明来源和时间。"
        ),
    )

    image_suggestions: list[str] = Field(
        default_factory=list,
        description=(
            "配图建议，2~4 条。每条描述一个适合插入的位置和画面内容，"
            "例如：『开头处配一张 AI Agent 自主调用工具的流程示意图』。"
            "只给建议，系统不会自动生成图片。"
        ),
    )

    sources: list[SourceReference] = Field(
        default_factory=list,
        description=(
            "文章实际参考过的来源列表。只填写你真的通过 web_search 找到、"
            "或通过 read_webpage 读过的网页。如果整个写作过程没有使用任何外部资料，"
            "返回空列表 []，不要为了凑数编造来源。"
        ),
    )

    risk_notes: list[str] = Field(
        default_factory=list,
        description=(
            "风险提示，供用户审稿时参考。至少考虑这几种情况："
            "资料不足、关键数据无法交叉验证、信息可能已经变化、"
            "某些观点存在争议、正文含有推测而无法证实。"
            "如果没有明显风险，返回空列表 []。"
        ),
    )
