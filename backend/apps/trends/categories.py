"""固定的热点分类与保守的标题规则。无法判断时归入“其他”。"""

from __future__ import annotations

import re
from apps.trends.research import MAX_FOCUS_TERMS


CATEGORIES = (
    ("focus", "关注方向"),
    ("technology", "科技"),
    ("finance", "财经"),
    ("sports", "体育"),
    ("entertainment", "娱乐"),
    ("society", "社会"),
    ("lifestyle", "生活"),
    ("other", "其他"),
)
CATEGORY_KEYS = {key for key, _label in CATEGORIES}
DEFAULT_FOCUS_TERMS = ["人工智能", "大模型", "生成式AI", "OpenAI", "ChatGPT"]

_RULES = (
    ("technology", r"人工智能|大模型|生成式.?ai|\b(?:ai|openai|chatgpt|claude|gemini|llm|nvidia|iphone|android)\b|芯片|机器人|科技|软件|半导体|量子|tech|robot|chip"),
    ("finance", r"股市|股票|基金|投资|银行|港卡|美股|纳指|美联储|利率|经济|汇率|黄金|比特币|加密货币|通胀|\b(?:btc|etf|gdp)\b|stock|market|bitcoin|crypto|nasdaq|wall street"),
    ("sports", r"足球|篮球|网球|比赛|联赛|世界杯|奥运|nba|nfl|mlb|fifa|uefa|goal|\bvs\.?\b|match|football|soccer|basketball|tennis|cricket"),
    ("entertainment", r"电影|电视剧|明星|演唱会|综艺|票房|音乐|歌手|影帝|影后|celebrity|movie|film|concert|netflix|hollywood|grammy"),
    ("society", r"选举|政策|法院|战争|地震|台风|灾害|政府|教育|医疗|选民|election|court|government|earthquake|storm|school"),
    ("lifestyle", r"旅游|旅行|美食|健康|穿搭|消费|购物|天气|饮食|travel|food|health|fashion|weather|recipe"),
)


def classify_title(title: str) -> str:
    text = title.casefold()
    for category, pattern in _RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return category
    return "other"


def clean_focus_terms(value) -> list[str]:
    if isinstance(value, str):
        terms = re.split(r"[,，\n|｜]+", value)
    elif isinstance(value, list):
        terms = value
    else:
        raise ValueError("关注方向应为关键词列表")
    cleaned = []
    for term in terms:
        if not isinstance(term, str):
            raise ValueError("关注方向只能包含文字")
        term = term.strip()
        if not term or term.casefold() in {entry.casefold() for entry in cleaned}:
            continue
        if len(term) > 40 or any(ord(char) < 32 for char in term):
            raise ValueError("每个关注词不能超过 40 字，且不能包含控制字符")
        cleaned.append(term)
    if not 1 <= len(cleaned) <= MAX_FOCUS_TERMS:
        raise ValueError(f"请填写 1 到 {MAX_FOCUS_TERMS} 个关注词")
    return cleaned


def default_focus_terms() -> list[str]:
    return list(DEFAULT_FOCUS_TERMS)


def default_auto_categories() -> list[str]:
    return ["focus"]
