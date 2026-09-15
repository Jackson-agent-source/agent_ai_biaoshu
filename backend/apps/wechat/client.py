"""
微信公众号 API 客户端。

★★★ 用这个客户端之前必须知道的三件事（全是踩过的坑）★★★

1. **微信的错误是 HTTP 200 + body 里的 errcode。**
   接口调用失败时，HTTP 状态码照样是 200，错误藏在响应体里：
       {"errcode": 40164, "errmsg": "invalid ip ..."}
   所以**只看 response.status_code 会漏掉所有错误**，必须解析 body。
   本模块每一个方法都检查 errcode，非 0 一律当失败处理。

2. **access_token 必须用 stable_token 拿，不能用老的 /cgi-bin/token。**
   老的 getAccessToken 每调用一次就把上一个 token 踢下线，两个 token 只有
   一个能用。而生产环境跑的是 3 个 gunicorn worker，各自有各自的缓存
   （CACHES_BACKEND=locmem），它们会互相把对方的 token 踢掉，
   表现是随机出现 40001 —— 而且是**偶发**的，最难查的那一类。
   stable_token 的 force_refresh=false 模式返回的是同一个 token，
   多 worker 天然安全。

3. **服务器 IP 必须在公众号的 IP 白名单里**，否则 40164。
   这是后台配置，代码层面无解，所以错误信息里要写清楚去哪儿加。

本模块所有方法**不抛异常**，失败时返回带 error（机器码）+ message（中文）的
结果对象 —— 和 apps/agent/tools/web_search.py 的 Provider 层一致。
把"抛不抛"的决定权交给上层，调用链上就不用每一层都写 try。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# 微信 API 的固定域名（公众号没有私有化部署，这个地址不需要配置）
API_BASE = "https://api.weixin.qq.com"

# token 缓存提前过期的秒数。
# 微信的 token 有效期 7200 秒，但我们不敢用到最后一刻 ——
# 一个请求可能在 token 还剩 2 秒时发出、5 秒后才到微信那边，
# 那就变成"缓存说还有效、微信用说已过期"。
# 提前 5 分钟作废，代价是每小时多拿一次 token（远远用不完配额）。
TOKEN_EXPIRY_MARGIN = 300


@dataclass
class WeChatResult:
    """一次微信 API 调用的结果。

    成功时 ok=True；失败时 ok=False，error 是机器码、message 是中文说明。

    一个对象装几种返回（token / media_id / url）看起来有点杂，
    但换来的是"每个方法返回同一个类型"，调用方不用记哪个方法返回什么。
    """

    ok: bool = False
    token: str = ""       # get_access_token 用
    media_id: str = ""    # upload_thumb / add_draft / update_draft 用
    url: str = ""         # upload_body_image 用
    errcode: int = 0
    error: str = ""       # 机器码，例如 wechat_ip_not_whitelisted
    message: str = ""     # 给用户看的中文

    @classmethod
    def failure(cls, error: str, message: str, errcode: int = 0) -> "WeChatResult":
        return cls(ok=False, error=error, message=message, errcode=errcode)


# ---------------------------------------------------------------------------
# 错误码翻译
# ---------------------------------------------------------------------------
# errcode -> (机器码, 中文说明)
#
# ★ 这些中文说明是**面向用户**的：每一条都要能让用户知道"我该去哪里改什么"。
#   "操作失败" 这种话对用户毫无价值，他只能来问你。
_ERRCODE_TABLE: dict[int, tuple[str, str]] = {
    40164: (
        "wechat_ip_not_whitelisted",
        "服务器的 IP 不在公众号的白名单里。请到 公众号后台 → 设置与开发 → "
        "基本配置 → IP 白名单，把服务器的公网 IP 加进去（最长 10 分钟生效）。",
    ),
    40125: (
        "wechat_bad_secret",
        "WECHAT_APP_SECRET 不正确。请到 公众号后台 → 设置与开发 → 基本配置 重新生成后填到 .env。",
    ),
    40013: (
        "wechat_bad_appid",
        "WECHAT_APP_ID 不正确。请核对 公众号后台 → 设置与开发 → 基本配置 里的 AppID。",
    ),
    40009: (
        "wechat_image_too_large",
        "图片太大了，微信拒收（正文图必须小于 1MB）。"
        "这不该发生 —— 说明压缩那一步有问题，请把这条日志发给开发者。",
    ),
    48001: (
        "wechat_api_unauthorized",
        "这个公众号没有调用该接口的权限。个人主体订阅号的部分接口（尤其是发表/发布相关）"
        "是不开放的，只能创建草稿、由你在草稿箱里手动发布。",
    ),
    45003: ("wechat_title_too_long", "标题超长，微信拒收（上限 64 个字符）。"),
    45004: ("wechat_digest_too_long", "摘要超长，微信拒收（上限 120 个字符）。"),
    45007: ("wechat_content_invalid", "正文内容被微信拒绝，可能是长度或格式问题。"),
    40007: (
        "wechat_invalid_media_id",
        "要更新的那份草稿已经不存在了（可能被你在后台删掉了）。请改成新建草稿。",
    ),
    41001: ("wechat_missing_token", "请求里缺少 access_token，请重试一次。"),
    42001: ("wechat_token_expired", "access_token 已过期，正在自动刷新，请重试一次。"),
    45009: ("wechat_api_quota_exceeded", "微信接口调用次数已达上限，请稍后再试。"),
    -1: ("wechat_busy", "微信服务器繁忙，请稍后重试。"),
}


def translate_error(errcode: int, errmsg: str = "") -> tuple[str, str]:
    """把微信的错误码翻成 (机器码, 中文说明)。表里没有的给一个兜底。"""
    if errcode in _ERRCODE_TABLE:
        return _ERRCODE_TABLE[errcode]
    detail = f"（微信原文：{errmsg}）" if errmsg else ""
    return "wechat_error", f"微信接口返回错误 {errcode}{detail}"


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class WeChatClient:
    """公众号 API 客户端。

    用法：
        client = WeChatClient(app_id=..., app_secret=...)
        result = client.upload_body_image(jpeg_bytes)
        if not result.ok:
            ...  # result.message 是可以直接给用户看的中文
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout: int = 60,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout

    @property
    def token_cache_key(self) -> str:
        # 带上 app_id：万一将来要同时支持多个公众号，
        # 不带 app_id 的 key 会让两个号互相拿到对方的 token。
        return f"wechat:access_token:{self.app_id}"

    # ------------------------------------------------------------------
    # access_token
    # ------------------------------------------------------------------
    def get_access_token(self, *, force_refresh: bool = False) -> WeChatResult:
        """取 access_token。

        ★ 用 /cgi-bin/stable_token 而不是 /cgi-bin/token，原因见模块开头第 2 条。

        force_refresh 的语义（微信官方定义，别搞反）：
            False → 优先返回当前有效的 token，**不会**让别的调用失效
            True  → 强制签发一个新的，旧的可能被作废
        所以正常路径永远用 False；只有真的收到 40001/42001 才用 True 重试一次。
        多 worker 并发时大家用的都是 False，就不会互相踢。
        """
        cache_key = self.token_cache_key

        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                return WeChatResult(ok=True, token=cached)

        payload = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
            "force_refresh": bool(force_refresh),
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{API_BASE}/cgi-bin/stable_token", json=payload)
        except httpx.TimeoutException:
            logger.warning("获取微信 access_token 超时")
            return WeChatResult.failure("wechat_timeout", "连接微信服务器超时，请稍后重试。")
        except httpx.RequestError as exc:
            logger.warning("获取微信 access_token 网络错误: %s", exc)
            return WeChatResult.failure(
                "wechat_unreachable", "无法连接微信服务器，请检查服务器能否访问外网。"
            )

        body = self._parse_json(response)
        if isinstance(body, WeChatResult):
            return body

        errcode = int(body.get("errcode") or 0)
        if errcode:
            error, message = translate_error(errcode, str(body.get("errmsg") or ""))
            logger.error("微信 stable_token 失败 errcode=%s", errcode)
            return WeChatResult.failure(error, message, errcode)

        token = str(body.get("access_token") or "")
        if not token:
            logger.error("微信 stable_token 返回里没有 access_token: %s", str(body)[:200])
            return WeChatResult.failure(
                "wechat_bad_response", "微信没有返回 access_token，请检查 AppID 和 AppSecret。"
            )

        expires_in = int(body.get("expires_in") or 7200)
        # 提前 5 分钟过期，并保证 TTL 至少 60 秒 ——
        # 万一微信返回一个很小的 expires_in，负数的 TTL 会让 cache.set 直接报错。
        ttl = max(60, expires_in - TOKEN_EXPIRY_MARGIN)
        cache.set(cache_key, token, ttl)

        logger.info("已获取微信 access_token（%s 秒后过期）", ttl)
        return WeChatResult(ok=True, token=token)

    def clear_token_cache(self) -> None:
        cache.delete(self.token_cache_key)

    # ------------------------------------------------------------------
    # 上传图片
    # ------------------------------------------------------------------
    def upload_body_image(self, data: bytes, filename: str = "image.jpg") -> WeChatResult:
        """上传**正文图**，返回 mmbiz.qpic.cn 地址。

        正文图片不允许外链，只能用这个接口返回的地址，否则手机上不显示。

        ★ 微信要求：jpg/png 且 **小于 1MB**（超了报 40009）。
          调用方必须先过一遍 image_utils.normalize_for_wechat_body()。
        """
        return self._upload(
            "/cgi-bin/media/uploadimg",
            data,
            filename=filename,
            content_type=_content_type_for(filename),
            want="url",
        )

    def upload_thumb(self, data: bytes, filename: str = "cover.jpg") -> WeChatResult:
        """上传**封面**，返回 thumb_media_id（draft/add 必填）。

        ★ 微信要求：**只收 JPG**，且约 **64KB**。
          调用方必须先过一遍 image_utils.make_wechat_thumb()。
        """
        return self._upload(
            "/cgi-bin/material/add_material",
            data,
            filename=filename,
            content_type="image/jpeg",
            params={"type": "thumb"},
            want="media_id",
        )

    def _upload(
        self,
        path: str,
        data: bytes,
        *,
        filename: str,
        content_type: str,
        params: dict[str, Any] | None = None,
        want: str,
    ) -> WeChatResult:
        if not data:
            return WeChatResult.failure("wechat_empty_file", "要上传的图片是空的。")

        # multipart 上传要带上 token
        files = {"media": (filename, data, content_type)}
        result = self._request(
            "POST", path, params=params, files=files, expect="media", want=want
        )
        return result

    # ------------------------------------------------------------------
    # 草稿
    # ------------------------------------------------------------------
    def add_draft(self, article: dict[str, Any]) -> WeChatResult:
        """新建草稿，返回 media_id。

        article 的字段（微信规定的，名字不能改）：
            title / author / digest / content / thumb_media_id / content_source_url

        ★ thumb_media_id（封面）是**必填**的。
          没有封面图就推不了 —— 这是微信的硬性要求，不是我们偷懒。
        """
        payload = {"articles": [article]}
        return self._request("POST", "/cgi-bin/draft/add", json_body=payload, expect="media_id")

    def update_draft(self, media_id: str, article: dict[str, Any], index: int = 0) -> WeChatResult:
        """更新已有草稿。

        ★ 为什么要有这个：用户会"改一版推一次"。
          如果每次都 add，草稿箱里会堆出一堆几乎一样的草稿，
          用户在手机上翻半天才能找到最新那份。
          走 update 的话，无论推几次，草稿箱里始终只有一份。

        index 是要替换第几篇（一篇草稿里可以有多篇文章，我们永远只有一篇，所以是 0）。
        """
        payload = {"media_id": media_id, "index": index, "articles": article}
        return self._request("POST", "/cgi-bin/draft/update", json_body=payload, expect="none")

    # ------------------------------------------------------------------
    # 内部：统一请求
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        expect: str = "none",
        want: str = "",
        _retried: bool = False,
    ) -> WeChatResult:
        """带 token 的请求，并处理"token 失效就刷新重试一次"。

        expect 说明成功时 body 里该有什么：
            "media_id" → 取 media_id
            "media"    → 看 want 是 url 还是 media_id
            "none"     → 什么都不取
        """
        token_result = self.get_access_token()
        if not token_result.ok:
            return token_result

        # ★ token 走查询参数而不是请求头 —— 这是微信的规定，
        #   不是我们写错了。所以日志里**绝不能**打印完整 URL（会带出 token）。
        query = dict(params or {})
        query["access_token"] = token_result.token

        url = f"{API_BASE}{path}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method, url, params=query, json=json_body, files=files
                )
        except httpx.TimeoutException:
            logger.warning("微信接口超时 path=%s", path)
            return WeChatResult.failure("wechat_timeout", "连接微信服务器超时，请稍后重试。")
        except httpx.RequestError as exc:
            logger.warning("微信接口网络错误 path=%s: %s", path, exc)
            return WeChatResult.failure(
                "wechat_unreachable", "无法连接微信服务器，请检查服务器能否访问外网。"
            )

        body = self._parse_json(response)
        if isinstance(body, WeChatResult):
            return body

        errcode = int(body.get("errcode") or 0)

        # ---- token 失效：清缓存 + 强制刷新 + 原样重试一次 ----
        # 只重试一次。无限重试会在真的配置错误时变成一个死循环，
        # 而且会一直打微信的接口（有可能触发频率限制）。
        if errcode in (40001, 42001, 41001) and not _retried:
            logger.warning("微信 token 失效（errcode=%s），强制刷新后重试一次", errcode)
            self.get_access_token(force_refresh=True)
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                files=files,
                expect=expect,
                want=want,
                _retried=True,
            )

        if errcode:
            error, message = translate_error(errcode, str(body.get("errmsg") or ""))
            logger.error("微信接口失败 path=%s errcode=%s", path, errcode)
            return WeChatResult.failure(error, message, errcode)

        # ---- 成功：按 expect 取内容 ----
        if expect == "media_id":
            media_id = str(body.get("media_id") or "")
            if not media_id:
                logger.error("微信返回里没有 media_id path=%s: %s", path, str(body)[:200])
                return WeChatResult.failure(
                    "wechat_bad_response", "微信没有返回预期的 media_id。"
                )
            return WeChatResult(ok=True, media_id=media_id)

        if expect == "media":
            if want == "url":
                image_url = str(body.get("url") or "")
                if not image_url:
                    logger.error("微信 uploadimg 没有返回 url: %s", str(body)[:200])
                    return WeChatResult.failure(
                        "wechat_bad_response", "微信没有返回图片地址。"
                    )
                return WeChatResult(ok=True, url=image_url)
            media_id = str(body.get("media_id") or "")
            if not media_id:
                logger.error("微信上传素材没有返回 media_id: %s", str(body)[:200])
                return WeChatResult.failure(
                    "wechat_bad_response", "微信没有返回素材 ID。"
                )
            return WeChatResult(ok=True, media_id=media_id)

        return WeChatResult(ok=True)

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any] | WeChatResult:
        """把响应解析成 dict；解析不了就返回一个失败结果。

        ★ 微信偶尔会在网关层返回 HTML 错误页（尤其是被限流的时候），
          这时 response.json() 会抛异常。不处理的话会变成一个 500，
          堆栈里看不出"其实是微信返回了 HTML"。
        """
        try:
            body = response.json()
        except Exception:
            logger.error(
                "微信返回内容不是 JSON（HTTP %s）: %s",
                response.status_code,
                response.text[:200],
            )
            return WeChatResult.failure(
                "wechat_bad_response",
                f"微信返回了无法解析的内容（HTTP {response.status_code}），请稍后重试。",
            )
        if not isinstance(body, dict):
            return WeChatResult.failure("wechat_bad_response", "微信返回了预期之外的数据结构。")
        return body


def _content_type_for(filename: str) -> str:
    return "image/png" if filename.lower().endswith(".png") else "image/jpeg"


def get_wechat_client() -> WeChatClient:
    """按配置造一个客户端。配置不全时抛 WeChatNotConfiguredError。

    ★ 这里**抛异常**而不是返回一个 NullClient，是刻意的：
      缺配置是调用方必须先解决的问题（不像搜索那样"可以没有"），
      让它立刻炸出来比一路传一个空对象下去更容易定位。
      异常是 AppError 子类，会被全局处理器转成 400 + 中文说明。
    """
    from apps.common.exceptions import WeChatNotConfiguredError

    app_id = (settings.WECHAT_APP_ID or "").strip()
    app_secret = (settings.WECHAT_APP_SECRET or "").strip()

    if not app_id or not app_secret:
        missing = [
            name
            for name, value in (("WECHAT_APP_ID", app_id), ("WECHAT_APP_SECRET", app_secret))
            if not value
        ]
        raise WeChatNotConfiguredError(
            f"还没有配置微信公众号，缺少：{'、'.join(missing)}。"
            "请在服务器根目录的 .env 里填好这两项，然后 docker compose up -d backend。"
        )

    return WeChatClient(
        app_id=app_id,
        app_secret=app_secret,
        timeout=settings.WECHAT_TIMEOUT,
    )
