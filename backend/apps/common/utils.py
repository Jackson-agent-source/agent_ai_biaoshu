"""通用小工具函数。放在这里避免各个 app 重复实现。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 中文字符也算 1 个长度单位（len() 本身就是这么算的，这里只是显式说明）
WHITESPACE_RE = re.compile(r"\s+")


def truncate(text: str | None, max_chars: int, suffix: str = "...(已截断)") -> str:
    """安全截断字符串。

    用途：ToolCall.result_preview、Source.snippet、read_webpage 正文限制。
    强调：我们不会把第三方网页全文永久存进数据库，一律先截断。
    """
    if not text:
        return ""
    text = str(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


def normalize_whitespace(text: str | None) -> str:
    """把连续空白（换行、制表符、多个空格）压成单个空格，用于摘要/预览。"""
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", str(text)).strip()


def extract_domain(url: str) -> str:
    """从 URL 里取出域名，例如 https://www.example.com/a/b -> example.com

    去掉 www. 前缀是为了让同一站点的来源在列表里看起来一致。
    """
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:  # pragma: no cover - urlparse 极少抛异常
        return ""
    # 去掉端口号
    netloc = netloc.split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def current_date_text() -> str:
    """返回给 Agent 看的"今天是几号"，帮助它判断什么算"最新"。"""
    today = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{today.strftime('%Y年%m月%d日')} {weekdays[today.weekday()]}"
