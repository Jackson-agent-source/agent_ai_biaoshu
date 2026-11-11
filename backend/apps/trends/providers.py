"""官方或平台提供的热点入口。只传输有限的元数据，不抓取视频正文。"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
from django.conf import settings
from django.utils.dateparse import parse_datetime
from apps.trends.categories import clean_focus_terms
from apps.trends.research import search_terms


@dataclass(frozen=True)
class TrendRecord:
    platform: str
    external_id: str
    title: str
    url: str
    region: str = ""
    rank: int = 0
    metric: int | None = None
    published_at: datetime | None = None
    research_topic: str = ""


class TrendProviderError(Exception):
    """对外只给安全的错误说明；不包含响应体、令牌或请求头。"""


def _get_json(url: str, *, headers=None, params=None, timeout=None) -> dict:
    try:
        with httpx.Client(timeout=timeout or settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TrendProviderError(f"平台请求失败（{type(exc).__name__}）") from exc


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_datetime(value) or parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def _number(value) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _traffic(value: str | None) -> int | None:
    text = (value or "").strip().upper().replace(",", "")
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMB]?)", text)
    if not match:
        return None
    return int(float(match.group(1)) * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2)])


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rss(platform: str, url: str, region: str = "") -> list[TrendRecord]:
    try:
        with httpx.Client(timeout=settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
            response = client.get(url, headers={"User-Agent": "AgentAITrends/1.0 (+RSS metadata)"})
            response.raise_for_status()
            if "text/html" in response.headers.get("content-type", "").lower():
                raise TrendProviderError(f"{platform} RSS 地址返回网页，暂无法采集")
            if len(response.content) > 2_000_000:
                raise TrendProviderError("RSS 超过大小限制")
            root = ElementTree.fromstring(response.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        raise TrendProviderError(f"{platform} RSS 获取失败（{type(exc).__name__}）") from exc
    records = []
    for rank, item in enumerate(root.findall("./channel/item")[:30], 1):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        guid = (item.findtext("guid") or link).strip()
        records.append(TrendRecord(platform, _stable_id(guid), title[:500], link[:1000], region, rank, None, _date(item.findtext("pubDate"))))
    return records


def fetch_bbc_world() -> list[TrendRecord]:
    return _rss("bbc_world", "https://feeds.bbci.co.uk/news/world/rss.xml", "INT")


def fetch_bbc_technology() -> list[TrendRecord]:
    return _rss("bbc_tech", "https://feeds.bbci.co.uk/news/technology/rss.xml", "INT")


def fetch_guardian_world() -> list[TrendRecord]:
    return _rss("guardian_world", "https://www.theguardian.com/world/rss", "INT")


def fetch_arxiv_ai() -> list[TrendRecord]:
    return _rss("arxiv_ai", "https://export.arxiv.org/rss/cs.AI", "INT")


def fetch_36kr() -> list[TrendRecord]:
    return _rss("kr36", "https://36kr.com/feed", "CN")


def fetch_hacker_news() -> list[TrendRecord]:
    ids = _get_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not isinstance(ids, list):
        raise TrendProviderError("Hacker News 榜单格式异常")
    records = []
    with httpx.Client(timeout=settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
        for item_id in ids[:20]:
            try:
                response = client.get(f"https://hacker-news.firebaseio.com/v0/item/{int(item_id)}.json")
                response.raise_for_status()
                item = response.json() or {}
            except (httpx.HTTPError, ValueError, TypeError):
                continue
            title = str(item.get("title") or "").strip()
            if not title or item.get("type") != "story":
                continue
            records.append(TrendRecord(
                "hacker_news", str(item_id), title[:500],
                f"https://news.ycombinator.com/item?id={int(item_id)}", "INT",
                len(records) + 1, _number(item.get("score")),
                datetime.fromtimestamp(item["time"], tz=timezone.utc) if item.get("time") else None,
            ))
    return records


def fetch_github() -> list[TrendRecord]:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    token = settings.TREND_GITHUB_TOKEN
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _get_json("https://api.github.com/search/repositories", headers=headers, params={
        "q": f"AI in:name,description created:>={since}", "sort": "stars", "order": "desc", "per_page": 20,
    })
    records = []
    for rank, item in enumerate((data.get("items") or [])[:20], 1):
        title = str(item.get("full_name") or "").strip()
        url = str(item.get("html_url") or "")
        if title and url.startswith("https://github.com/"):
            records.append(TrendRecord("github", str(item.get("id")), title[:500], url[:1000], "INT", rank, _number(item.get("stargazers_count")), _date(item.get("created_at"))))
    return records


def fetch_gitee() -> list[TrendRecord]:
    params = {"q": "AI", "per_page": 20}
    if settings.TREND_GITEE_TOKEN:
        params["access_token"] = settings.TREND_GITEE_TOKEN
    data = _get_json("https://gitee.com/api/v5/search/repositories", params=params)
    if not isinstance(data, list):
        raise TrendProviderError("Gitee 搜索接口格式异常")
    records = []
    for rank, item in enumerate(data[:20], 1):
        title = str(item.get("full_name") or "").strip()
        url = str(item.get("html_url") or "")
        if title and url.startswith("https://gitee.com/"):
            records.append(TrendRecord("gitee", str(item.get("id")), title[:500], url[:1000], "CN", rank, _number(item.get("stargazers_count")), _date(item.get("updated_at"))))
    return records


def fetch_google() -> list[TrendRecord]:
    geo = settings.TREND_GOOGLE_GEO
    if len(geo) != 2 or not geo.isalpha():
        raise TrendProviderError("Google 地区代码必须是两个字母")
    try:
        with httpx.Client(timeout=settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
            response = client.get("https://trends.google.com/trending/rss", params={"geo": geo})
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise TrendProviderError("Google RSS 超过大小限制")
            root = ElementTree.fromstring(response.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        raise TrendProviderError(f"Google RSS 获取失败（{type(exc).__name__}）") from exc

    records = []
    for rank, item in enumerate(root.findall("./channel/item")[:30], 1):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url.startswith("https://"):
            continue
        traffic = next((el.text for el in item if el.tag.endswith("approx_traffic")), None)
        metric = _traffic(traffic)
        records.append(TrendRecord("google", _stable_id(f"{geo}:{title.casefold()}"), title[:500], url[:1000], geo, rank, metric, _date(item.findtext("pubDate"))))
    return records


class FocusNewsBatch(list):
    """兼容原有记录列表，同时携带部分方向失败的安全提示。"""
    def __init__(self, records, warnings):
        super().__init__(records)
        self.warnings = warnings


def _focus_news_for_term(term: str, user_id: int) -> list[TrendRecord]:
    aliases = [value.replace('"', '').strip() for value in search_terms(term)]
    query = "(" + " OR ".join(f'"{value}"' for value in aliases) + ") when:7d"
    try:
        with httpx.Client(timeout=settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
            response = client.get("https://news.google.com/rss/search", params={
                "q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans",
            })
            response.raise_for_status()
            if "text/html" in response.headers.get("content-type", "").lower():
                raise TrendProviderError("关注方向资讯接口返回网页，请检查网络或稍后重试")
            if len(response.content) > 2_000_000:
                raise TrendProviderError("关注方向资讯 RSS 超过大小限制")
            root = ElementTree.fromstring(response.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        raise TrendProviderError(f"关注方向资讯获取失败（{type(exc).__name__}）") from exc

    records = []
    for rank, item in enumerate(root.findall("./channel/item")[:6], 1):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url.startswith("https://"):
            continue
        guid = (item.findtext("guid") or url).strip()
        records.append(TrendRecord(
            "focus_news", _stable_id(f"{user_id}:{guid}"), title[:500], url[:1000], "CN", rank,
            None, _date(item.findtext("pubDate")),
            research_topic=term,
        ))
    return records


def fetch_focus_news(terms: list[str], user_id: int) -> list[TrendRecord]:
    """每个方向独立检索，最多四路并发；轮询合并，避免热门方向淹没其他方向。"""
    terms = clean_focus_terms(terms)
    batches, warnings = [], []
    with ThreadPoolExecutor(max_workers=min(4, len(terms))) as executor:
        futures = [executor.submit(_focus_news_for_term, term, user_id) for term in terms]
        for term, future in zip(terms, futures):
            try:
                batches.append(future.result())
            except TrendProviderError as exc:
                warnings.append(f"{term}：{exc}")
    if not batches:
        detail = warnings[0] if warnings else "请稍后重试"
        raise TrendProviderError(f"所有研究方向资讯获取失败；{detail}")
    records, seen = [], set()
    for index in range(max((len(batch) for batch in batches), default=0)):
        for batch in batches:
            if index < len(batch) and batch[index].external_id not in seen:
                record = batch[index]
                records.append(record)
                seen.add(record.external_id)
    return FocusNewsBatch(records, warnings)


def fetch_youtube() -> list[TrendRecord]:
    if not settings.TREND_YOUTUBE_API_KEY:
        raise TrendProviderError("未配置 YouTube API Key")
    data = _get_json("https://www.googleapis.com/youtube/v3/videos", params={
        "key": settings.TREND_YOUTUBE_API_KEY,
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": settings.TREND_YOUTUBE_REGION,
        "maxResults": 30,
    })
    records = []
    for rank, item in enumerate(data.get("items") or [], 1):
        video_id = str(item.get("id") or "")
        snippet = item.get("snippet") or {}
        title = str(snippet.get("title") or "").strip()
        if not video_id or not title:
            continue
        records.append(TrendRecord("youtube", video_id[:200], title[:500], f"https://www.youtube.com/watch?v={quote(video_id)}", settings.TREND_YOUTUBE_REGION, rank, _number((item.get("statistics") or {}).get("viewCount")), _date(snippet.get("publishedAt"))))
    return records


def fetch_x() -> list[TrendRecord]:
    if not settings.TREND_X_BEARER_TOKEN:
        raise TrendProviderError("未配置 X Bearer Token")
    woeid = settings.TREND_X_WOEID
    if woeid < 1:
        raise TrendProviderError("X WOEID 配置不正确")
    data = _get_json(f"https://api.x.com/2/trends/by/woeid/{woeid}", headers={"Authorization": f"Bearer {settings.TREND_X_BEARER_TOKEN}"})
    records = []
    for rank, item in enumerate((data.get("data") or [])[:30], 1):
        title = str(item.get("trend_name") or item.get("name") or "").strip()
        if not title:
            continue
        records.append(TrendRecord("x", _stable_id(f"{woeid}:{title.casefold()}"), title[:500], f"https://x.com/search?q={quote(title)}", str(woeid), rank, _number(item.get("tweet_count") or item.get("post_count"))))
    return records


def fetch_douyin() -> list[TrendRecord]:
    if not (settings.TREND_DOUYIN_CLIENT_KEY and settings.TREND_DOUYIN_CLIENT_SECRET):
        raise TrendProviderError("未配置抖音应用凭证或 hotsearch 权限")
    try:
        with httpx.Client(timeout=settings.TREND_FETCH_TIMEOUT, follow_redirects=False) as client:
            token_response = client.post("https://open.douyin.com/oauth/stable_client_token/", json={
                "client_key": settings.TREND_DOUYIN_CLIENT_KEY,
                "client_secret": settings.TREND_DOUYIN_CLIENT_SECRET,
                "grant_type": "client_credential",
            })
            token_response.raise_for_status()
            token_data = token_response.json().get("data") or {}
    except (httpx.HTTPError, ValueError) as exc:
        raise TrendProviderError(f"抖音令牌获取失败（{type(exc).__name__}）") from exc
    token = token_data.get("access_token")
    if token_data.get("error_code") or not token:
        raise TrendProviderError("抖音应用认证失败")
    data = _get_json("https://open.douyin.com/hotsearch/sentences/", headers={"access-token": token})
    payload = data.get("data") or {}
    if payload.get("error_code"):
        raise TrendProviderError("抖音热点接口拒绝请求，请确认 hotsearch 权限")
    records = []
    for rank, item in enumerate((payload.get("list") or [])[:30], 1):
        title = str(item.get("sentence") or "").strip()
        if not title:
            continue
        records.append(TrendRecord("douyin", _stable_id(title.casefold()), title[:500], f"https://www.douyin.com/search/{quote(title)}", "CN", rank, _number(item.get("hot_level"))))
    return records


PROVIDERS = {
    "google": fetch_google, "hacker_news": fetch_hacker_news,
    "bbc_world": fetch_bbc_world, "bbc_tech": fetch_bbc_technology,
    "guardian_world": fetch_guardian_world, "arxiv_ai": fetch_arxiv_ai,
    "kr36": fetch_36kr, "github": fetch_github,
    "gitee": fetch_gitee, "youtube": fetch_youtube, "x": fetch_x, "douyin": fetch_douyin,
}

SOURCE_LABELS = {
    "focus_news": "关注方向 · Google News", "google": "Google Trends",
    "hacker_news": "Hacker News", "bbc_world": "BBC 世界", "bbc_tech": "BBC 科技",
    "guardian_world": "The Guardian 世界", "arxiv_ai": "arXiv 人工智能论文",
    "kr36": "36氪", "github": "GitHub 新项目", "gitee": "Gitee 项目",
    "youtube": "YouTube", "x": "X", "douyin": "抖音",
}
