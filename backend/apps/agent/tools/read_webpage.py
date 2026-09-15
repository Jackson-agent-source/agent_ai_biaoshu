"""
工具 2：read_webpage —— 读取网页正文。

为什么必须有这个工具？
    搜索结果里的 snippet 只有一两句话，经常断章取义。
    要写出可信的文章，Agent 必须能真的打开原页面读正文。

处理流程（严格按顺序，每一步都有原因）：
    URL 安全校验       -> 防 SSRF，只用 http/https，禁内网
    发起 HTTP 请求     -> 带超时、带 UA、限制重定向
    检查 Content-Type  -> 只读文本类内容，不下载 exe/zip/图片
    限制响应体大小     -> 边下边数，超过上限就断开
    提取正文           -> trafilatura 为主，BeautifulSoup 兜底
    清理噪音           -> 去掉导航栏、脚本、菜单、广告
    长度截断           -> 默认 20000 字符，防止撑爆模型上下文
    返回结构化结果     -> 并登记进 SourceRegistry

安全底线：**绝不把原始 HTML 丢给大模型**，也**不把网页全文存进数据库**。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from django.conf import settings
from langchain_core.tools import tool

from apps.agent.services.source_registry import register_source
from apps.agent.tools.url_safety import validate_public_url
from apps.common.exceptions import UnsafeUrlError
from apps.common.utils import extract_domain, normalize_whitespace

logger = logging.getLogger(__name__)

# 只处理这些 Content-Type。其余（图片、视频、压缩包、可执行文件）一律拒绝。
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml",
    "text/plain",
    "application/xml",
    "text/xml",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 AIContentAgent/1.0"
)

# 正文提取后要干掉的残留噪声（兜底用）
NOISE_PATTERNS = [
    re.compile(r"^\s*(登录|注册|首页|上一页|下一页|返回顶部|扫码关注|点击关注)\s*$"),
    re.compile(r"^\s*(Cookie|cookie)\s*(政策|声明|设置).*$"),
]


@tool
def read_webpage(url: str) -> dict[str, Any]:
    """打开一个网页，读取并返回它的正文内容。

    什么时候用：当你需要引用某个页面的具体数据、观点、原文表述时。
    先从 web_search 结果里挑出看起来最有价值的链接，再用这个工具读原文。

    使用建议：
    - 一次只读一个链接，优先读权威来源（官方公告、主流媒体、原始报告）。
    - 如果某条搜索结果的标题和摘要已经足够支撑你的论点，可以不必逐条打开。
    - 同一个链接不要重复读取，内容是一样的，白白浪费时间。

    Args:
        url: 要读取的网页地址，必须以 http:// 或 https:// 开头。
            通常直接使用 web_search 返回结果里的 url 字段。

    Returns:
        成功时：
            {"success": true, "url": "...", "title": "...", "text": "正文...",
             "word_count": 1234, "truncated": false, "domain": "example.com"}
        失败时：
            {"success": false, "error": "错误码", "message": "中文说明"}
        失败是正常情况（网站反爬、超时、需要登录），换一个来源继续即可。
    """
    url = (url or "").strip()
    if not url:
        return {"success": False, "error": "empty_url", "message": "链接不能为空。"}

    limits = settings.AGENT_LIMITS

    # ---------- 第 1 步：安全校验 ----------
    try:
        safe_url = validate_public_url(url)
    except UnsafeUrlError as exc:
        logger.warning("read_webpage 拒绝不安全链接 url=%s reason=%s", url, exc.message)
        return {"success": False, "error": "unsafe_url", "message": exc.message}

    logger.info("read_webpage started url=%s", safe_url)

    # ---------- 第 2~4 步：请求 + 校验类型 + 限制大小 ----------
    try:
        html, final_url, content_type = _fetch_html(safe_url)
    except UnsafeUrlError as exc:
        # 重定向过程中发现了内网地址
        return {"success": False, "error": "unsafe_redirect", "message": exc.message}
    except httpx.TimeoutException:
        logger.warning("read_webpage 超时 url=%s", safe_url)
        return {"success": False, "error": "timeout", "message": "网页响应超时，请换一个来源。"}
    except httpx.TooManyRedirects:
        return {"success": False, "error": "too_many_redirects", "message": "网页重定向次数过多。"}
    except httpx.HTTPStatusError as exc:
        return {
            "success": False,
            "error": f"http_{exc.response.status_code}",
            "message": f"网页返回状态码 {exc.response.status_code}，无法读取。",
        }
    except httpx.RequestError as exc:
        logger.warning("read_webpage 请求失败 url=%s err=%s", safe_url, exc)
        return {"success": False, "error": "request_failed", "message": "无法连接到该网页。"}
    except ValueError as exc:
        # 非文本类型、体积超限等
        return {"success": False, "error": "unsupported_content", "message": str(exc)}

    # ---------- 第 5~7 步：提取正文 + 清理 + 截断 ----------
    title, text = _extract_content(html, final_url)

    if not text or len(text) < 50:
        logger.info("read_webpage 未能提取到有效正文 url=%s", final_url)
        return {
            "success": False,
            "error": "no_content_extracted",
            "message": "未能从该页面提取到有效正文（可能是动态渲染页面或需要登录）。",
        }

    max_chars = limits["MAX_WEBPAGE_CHARS"]
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    # ---------- 第 8 步：登记来源 ----------
    register_source(
        url=final_url,
        title=title,
        snippet=normalize_whitespace(text[:500]),
        source_type="webpage",
        was_read=True,  # 标记"这个来源我们真的读过全文"
    )

    logger.info("read_webpage completed url=%s chars=%s truncated=%s", final_url, len(text), truncated)

    return {
        "success": True,
        "url": final_url,
        "title": title,
        "text": text,
        "word_count": len(text),
        "truncated": truncated,
        "domain": extract_domain(final_url),
        "content_type": content_type,
        "note": (
            "如果内容被截断，说明原文更长；引用数据时请只使用你确实读到的部分。"
            if truncated
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------
def _fetch_html(url: str) -> tuple[str, str, str]:
    """请求网页并返回 (html, 最终URL, content_type)。

    自己实现重定向循环，而不是用 httpx 的 follow_redirects=True，
    因为每一次跳转的目标地址都必须重新做安全校验 —— 否则攻击者可以用
    一个公网域名 302 跳转到 http://169.254.169.254/ 来绕过我们的检查。
    """
    limits = settings.AGENT_LIMITS
    max_bytes = limits["WEBPAGE_MAX_BYTES"]
    max_redirects = limits["WEBPAGE_MAX_REDIRECTS"]
    timeout = limits["WEBPAGE_FETCH_TIMEOUT"]

    current_url = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(max_redirects + 1):
            with client.stream("GET", current_url, headers=headers) as response:
                # --- 重定向 ---
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("网页返回了重定向，但没有给出目标地址。")
                    # 相对地址转绝对地址
                    next_url = urljoin(current_url, location)
                    # ★ 每一次跳转都重新做安全校验
                    current_url = validate_public_url(next_url)
                    continue

                response.raise_for_status()

                # --- Content-Type 检查 ---
                content_type = (response.headers.get("content-type") or "").lower()
                if content_type and not any(allowed in content_type for allowed in ALLOWED_CONTENT_TYPES):
                    raise ValueError(
                        f"该链接不是网页文本（Content-Type: {content_type.split(';')[0]}），已跳过。"
                    )

                # --- 边下边限制体积 ---
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        logger.warning("网页体积超过上限 %s 字节，已截断 url=%s", max_bytes, current_url)
                        break

                raw = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                try:
                    html = raw.decode(encoding, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")

                return html, current_url, content_type or "text/html"

        raise httpx.TooManyRedirects(f"重定向次数超过 {max_redirects} 次")


def _extract_content(html: str, url: str) -> tuple[str, str]:
    """从 HTML 中提取 (标题, 正文纯文本)。

    优先用 trafilatura（专门为"正文提取"训练的，效果最好），
    它失败时退回 BeautifulSoup 做一个朴素但可靠的清理。
    """
    title = ""
    text = ""

    # ---------- 方案 A：trafilatura ----------
    try:
        import trafilatura

        # 先拿标题
        try:
            metadata = trafilatura.extract_metadata(html, default_url=url)
            if metadata and metadata.title:
                title = normalize_whitespace(metadata.title)
        except Exception:
            logger.debug("trafilatura 提取标题失败，稍后用 <title> 兜底", exc_info=True)

        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="txt",
            include_comments=False,   # 评论区噪音大，不要
            include_tables=True,      # 表格里经常有数据，要保留
            favor_precision=True,     # 宁可少一点，也不要混进导航栏
        )
        if extracted:
            text = extracted
    except Exception:
        logger.exception("trafilatura 提取正文失败，改用 BeautifulSoup")

    # ---------- 方案 B：BeautifulSoup 兜底 ----------
    if not text or len(text.strip()) < 50:
        fallback_title, fallback_text = _extract_with_bs4(html)
        title = title or fallback_title
        if len(fallback_text) > len(text):
            text = fallback_text

    # ---------- 兜底标题：<title> 标签或域名 ----------
    if not title:
        title = _title_from_html(html) or extract_domain(url)

    return title, _clean_text(text)


def _extract_with_bs4(html: str) -> tuple[str, str]:
    """用 BeautifulSoup 做朴素提取：删掉噪音标签，然后取纯文本。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return "", ""

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = normalize_whitespace(soup.title.string)

    # 这些标签里的内容都不是正文，直接删掉
    for tag in soup(
        ["script", "style", "noscript", "iframe", "svg", "form",
         "nav", "header", "footer", "aside", "button", "select"]
    ):
        tag.decompose()

    # 常见导航/侧边栏容器（按 class / id 猜测）
    for selector in ("nav", "menu", "sidebar", "comment", "footer", "header", "advert", "share"):
        for tag in soup.find_all(attrs={"class": re.compile(selector, re.I)}):
            tag.decompose()
        for tag in soup.find_all(attrs={"id": re.compile(selector, re.I)}):
            tag.decompose()

    # 优先取语义化容器，取不到就整页
    container = soup.find("article") or soup.find("main") or soup.body or soup
    return title, container.get_text(separator="\n")


def _title_from_html(html: str) -> str:
    """最朴素的标题提取：正则找 <title>。"""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return normalize_whitespace(match.group(1)) if match else ""


def _clean_text(text: str) -> str:
    """清理纯文本：压缩空行、去掉导航残留、去掉整块重复的正文。"""
    if not text:
        return ""

    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 处理 Markdown 标题、列表符号等正常格式，只剔除明显的导航残留
        if any(pattern.match(line) for pattern in NOISE_PATTERNS):
            continue
        lines.append(line)

    lines = _collapse_duplicate_block(lines)

    cleaned = "\n".join(lines)
    # 连续 3 个以上换行压成 2 个
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# 只有当重复块的字符数超过这个值时，才认为是"异常重复"而不是有意的重复排版
MIN_DUPLICATE_BLOCK_CHARS = 80


def _collapse_duplicate_block(lines: list[str]) -> list[str]:
    """把"尾部整块重复"的正文合并成一份。

    为什么需要这个？
    trafilatura 在 precision 模式下，遇到某些页面结构（例如正文同时被
    <main> 和 <article> 包裹、页面内容又比较少）时，会把正文原样输出两遍，
    变成「标题 / 第一段 / 第二段 / 第一段 / 第二段」。
    我们在真实站点上验证过它多数情况正常，但一旦触发，
    生成的文章里就会出现成段重复的内容 —— 这对质量是致命的。

    这里处理的是最常见的形态：结尾处恰好是同一个块连着出现两次。
    例如 9 行 = [标题] + [p1 p2 p3 p4] + [p1 p2 p3 p4]，
    会被合并成 [标题] + [p1 p2 p3 p4]。

    为避免误伤（比如文章里本来就有两行相同的分隔线），
    要求重复块的字符数超过 MIN_DUPLICATE_BLOCK_CHARS 才动手。
    """
    if len(lines) < 2:
        return lines

    total = len(lines)
    # 从最长的可能重复块开始尝试，找到就合并
    for size in range(total // 2, 0, -1):
        block = lines[total - size * 2 : total - size]
        if block != lines[total - size : total]:
            continue
        if sum(len(line) for line in block) < MIN_DUPLICATE_BLOCK_CHARS:
            # 块太短，可能只是正常的重复排版，不处理
            continue
        return lines[: total - size]
    return lines
