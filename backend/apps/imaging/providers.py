"""
生图服务 Provider 层。

写法完全对齐 apps/agent/tools/web_search.py 里那套搜索 Provider：

    1. 抽出 ImageProvider 协议 —— 以后换厂商只加一个类，服务和 API 都不用动。
    2. **永不抛异常**。失败时返回带 error（机器码）+ message（中文）的结果，
       由上层决定怎么处理。抛异常会把调用链上每一层都逼着写 try。
    3. request_url 属性里**不含 key**，所以可以直接放进错误信息里帮用户定位配置问题。
    4. httpx.Client 当上下文管理器用，跟请求生命周期对齐。

★ 为什么必须同时支持 b64_json 和 url 两种返回：

    用户走的是**中转站**（one-api / new-api 那类）。这类中转站即使你明确申请
    response_format=b64_json，也经常原样把上游的 url 透传回来；
    反过来也有上游只给 url 的情况。
    所以"先找 b64_json，没有就找 url 并下载"不是兼容性优化，是**必需路径**。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from django.conf import settings
from apps.accounts.connections import personal_connection
from apps.accounts.transport import public_client

logger = logging.getLogger(__name__)

# 从 url 下载图片时的硬上限。生图模型输出通常几百 KB 到几 MB，
# 20MB 是"明显不对劲"的界线 —— 挡的是中转站返回错误页面/超大文件把内存吃满。
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 只允许 http/https。这两个之外的协议（file:// data:// 等）
# 不该出现在这条链路上，直接拒掉。
ALLOWED_URL_SCHEMES = ("http", "https")


@dataclass
class ImageGenerationResult:
    """一次生图的结果。

    error 非空表示失败；成功时 data 是图片字节、revised_prompt 可能为空。
    """

    data: bytes = b""
    revised_prompt: str = ""
    error: str = ""     # 机器可读的错误码，前端/日志用
    message: str = ""   # 给用户看的中文说明

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.data)


class ImageProvider(Protocol):
    """生图服务提供方需要实现的接口。"""

    name: str

    def generate(self, prompt: str) -> ImageGenerationResult:
        ...


class OpenAICompatibleImageProvider:
    """OpenAI 兼容的 /images/generations 接口（中转站走的就是这个）。

    配置项全部来自环境变量：
        IMAGE_API_KEY / IMAGE_BASE_URL / IMAGE_MODEL / IMAGE_SIZE /
        IMAGE_QUALITY / IMAGE_OUTPUT_FORMAT / IMAGE_TIMEOUT
    """

    name = "openai-compatible"
    default_base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model: str = "gpt-image-1",
        timeout: int = 300,
        size: str = "1024x1536",
        quality: str = "medium",
        output_format: str = "jpeg",
    ) -> None:
        self.api_key = api_key
        # rstrip("/") 很关键：用户很容易填成 https://xx/v1/，
        # 不去掉就会拼出 //images/generations，有的网关直接 404。
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.size = size
        self.quality = quality
        self.output_format = output_format

    @property
    def request_url(self) -> str:
        """真正会被请求的地址。

        只有地址、**不含 api_key**（key 走 Authorization 头），
        所以把它写进错误信息是安全的 —— 这一点和 Tavily Provider 一样。
        """
        return f"{self.base_url}/images/generations"

    # ------------------------------------------------------------------
    # 请求体
    # ------------------------------------------------------------------
    def _build_payload(self, prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": self.size,
        }
        # quality / output_format 是 gpt-image-* 系列才有的参数。
        # 老一代模型（dall-e-3 等）收到会直接 400，
        # 所以只在模型名看起来是 gpt-image 时才带上。
        if "gpt-image" in self.model.lower():
            payload["quality"] = self.quality
            payload["output_format"] = self.output_format
        return payload

    def generate(self, prompt: str) -> ImageGenerationResult:
        """调用 /images/generations 并取回图片字节。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with self._client() as client:
                response = client.post(
                    self.request_url, json=self._build_payload(prompt), headers=headers
                )
        except httpx.TimeoutException:
            # 生图本身就慢（30~90 秒很正常），超时是最常见的失败
            logger.warning("生图超时 url=%s model=%s", self.request_url, self.model)
            return ImageGenerationResult(
                error="image_provider_timeout",
                message=(
                    f"生图服务响应超时（已等待 {self.timeout} 秒）。"
                    "可以调大 IMAGE_TIMEOUT，或者换一个更快的 IMAGE_MODEL。"
                ),
            )
        except httpx.RequestError as exc:
            logger.warning("生图网络错误: %s", exc)
            return ImageGenerationResult(
                error="image_provider_unreachable",
                message=(
                    f"无法连接生图服务（请求地址 {self.request_url}）。"
                    "请检查 IMAGE_BASE_URL 填写是否正确、服务器能否访问外网。"
                ),
            )

        if response.status_code >= 400:
            return self._translate_http_error(response)

        try:
            body = response.json()
        except Exception:
            logger.exception("生图服务返回内容不是合法 JSON")
            return ImageGenerationResult(
                error="image_provider_bad_response",
                message="生图服务返回的内容无法解析，可能 IMAGE_BASE_URL 指向了错误的地址。",
            )

        return self._extract_image(body)

    def _client(self):
        return httpx.Client(timeout=self.timeout)

    # ------------------------------------------------------------------
    # 取图：b64_json 优先，其次 url
    # ------------------------------------------------------------------
    def _extract_image(self, body: dict[str, Any]) -> ImageGenerationResult:
        # 有的中转站在 HTTP 200 的 body 里塞错误（和微信一个套路），必须先查
        if isinstance(body.get("error"), dict):
            return self._translate_body_error(body["error"])

        items = body.get("data") or []
        if not isinstance(items, list) or not items:
            logger.error("生图返回里没有 data 数组: %s", str(body)[:300])
            return ImageGenerationResult(
                error="image_provider_bad_response",
                message=(
                    "生图服务没有返回图片数据。"
                    f"请确认 IMAGE_MODEL（当前是 {self.model}）是中转站支持的**生图**模型。"
                ),
            )

        first = items[0] or {}
        revised = str(first.get("revised_prompt") or "")

        # ---- 路径 1：base64（我们期望的）----
        b64 = first.get("b64_json")
        if b64:
            try:
                # 中转站有时会带上 "data:image/png;base64," 前缀，要先剥掉
                if isinstance(b64, str) and b64.startswith("data:"):
                    b64 = b64.split(",", 1)[-1]
                return ImageGenerationResult(
                    data=base64.b64decode(b64), revised_prompt=revised
                )
            except Exception:
                logger.exception("生图返回的 b64_json 无法解码")
                return ImageGenerationResult(
                    error="image_provider_bad_response",
                    message="生图服务返回的图片数据无法解码。",
                )

        # ---- 路径 2：url（中转站更常给这个）----
        url = str(first.get("url") or "").strip()
        if url:
            return self._download(url, revised)

        logger.error("生图返回里既没有 b64_json 也没有 url: %s", str(first)[:300])
        return ImageGenerationResult(
            error="image_provider_bad_response",
            message="生图服务返回里既没有图片数据也没有图片地址。",
        )

    def _download(self, url: str, revised: str) -> ImageGenerationResult:
        """把图片从 url 下载回来。

        安全说明：这个 url 来自**管理员自己配置的中转站**的响应，
        不是终端用户可控的输入，所以这里做的是**按比例的**防护 ——
        协议白名单 + 大小上限 + 超时，而不是完整的 SSRF 校验
        （那种校验要求目标是公网地址，会把自建在内网的网关误杀）。
        真正要防的是"返回一个巨大的响应把内存吃满"和"返回 file:// 之类的协议"。
        """
        scheme = (urlparse(url).scheme or "").lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            logger.error("生图服务返回的图片地址协议不被允许: %s", scheme or "(空)")
            return ImageGenerationResult(
                error="image_provider_bad_response",
                message=f"生图服务返回的图片地址协议异常（{scheme or '空'}）。",
            )

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.content
        except httpx.TimeoutException:
            logger.warning("下载生图结果超时 url=%s", url)
            return ImageGenerationResult(
                error="image_provider_timeout", message="下载生成的图片超时。"
            )
        except httpx.RequestError as exc:
            logger.warning("下载生图结果失败: %s", exc)
            return ImageGenerationResult(
                error="image_provider_unreachable", message="无法下载生成的图片。"
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("下载生图结果返回 %s", exc.response.status_code)
            return ImageGenerationResult(
                error="image_provider_error",
                message=f"下载生成的图片失败（HTTP {exc.response.status_code}）。",
            )

        if not data:
            return ImageGenerationResult(
                error="image_provider_bad_response", message="生图服务返回的图片是空的。"
            )
        if len(data) > MAX_DOWNLOAD_BYTES:
            logger.error("下载的图片过大 %s 字节，已拒绝", len(data))
            return ImageGenerationResult(
                error="image_provider_bad_response",
                message="生图服务返回的图片过大，已拒绝处理。",
            )

        return ImageGenerationResult(data=data, revised_prompt=revised)

    # ------------------------------------------------------------------
    # 错误翻译
    # ------------------------------------------------------------------
    def _translate_http_error(self, response: httpx.Response) -> ImageGenerationResult:
        """把 HTTP 错误码翻成用户能照着改配置的中文。"""
        detail = ""
        code = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            code = str(error.get("code") or "")
            detail = str(error.get("message") or "")
        except Exception:
            detail = response.text[:200]

        logger.error(
            "生图服务返回 %s code=%s: %s",
            response.status_code,
            code or "-",
            detail[:300],
        )

        if response.status_code == 401:
            return ImageGenerationResult(
                error="image_provider_unauthorized",
                message="生图服务的密钥无效（HTTP 401），请检查 .env 里的 IMAGE_API_KEY。",
            )
        if response.status_code == 429:
            return ImageGenerationResult(
                error="image_provider_rate_limited",
                message="生图服务请求过于频繁或额度不足（HTTP 429），请稍后再试。",
            )
        # ★ 403 也要走 body 翻译。中转站习惯把"上游欠费"「分组无权限」这类
        #   业务错误塞在 403 里返回，只认 400/404 的话，detail 明明已经解析出来了
        #   却会被丢掉，用户最后只看到一句没有信息量的"HTTP 403"。
        if response.status_code in (400, 403, 404):
            return self._translate_body_error({"code": code, "message": detail})

        return ImageGenerationResult(
            error="image_provider_error",
            message=f"生图服务返回错误（HTTP {response.status_code}，请求地址 {self.request_url}）。",
        )

    def _translate_body_error(self, error: dict[str, Any]) -> ImageGenerationResult:
        """翻译 body 里的 error 对象。

        ★ 最值得单独说两句的是这两类（中转站上非常常见）：
            · 模型不存在 / 没有权限 —— 用户填错了 IMAGE_MODEL
            · 余额不足 / 配额用完 —— 中转站账户没钱了
        这两种光看"HTTP 400"是猜不出来的，所以必须**指名道姓地说该改哪个变量**。
        """
        code = str(error.get("code") or "").lower()
        detail = str(error.get("message") or error.get("type") or "")
        low = detail.lower()

        logger.error("生图服务业务错误 code=%s: %s", code or "-", detail[:300])

        # "model not found" / "the model `xxx` does not exist" / "unknown model"
        if "model" in low and ("not found" in low or "exist" in low or "unknown" in low):
            return ImageGenerationResult(
                error="image_provider_bad_model",
                message=(
                    f"生图服务说模型不存在（当前 IMAGE_MODEL={self.model}）。"
                    "请去中转站后台确认它支持的**生图**模型名，注意生图模型和对话模型是两套。"
                ),
            )
        if "insufficient" in low or "quota" in low or "balance" in low or "credit" in low:
            # ★ 这句"余额不足"不一定是在说你。中转站普遍是二层转售：它自己接
            #   OpenAI 的那条上游线路欠费时，也会把上游的原话原样转给用户，
            #   看起来和"你欠费了"一模一样。所以必须把原话带出来，并点明
            #   "你余额正常的话这就是中转站的问题，改配置没用"——
            #   否则用户会一直去后台充钱、反复检查 Key，永远查不到真正的原因。
            return ImageGenerationResult(
                error="image_provider_quota_exceeded",
                message=(
                    f"生图服务返回额度不足：{detail or '未提供原因'}。"
                    "请先确认你自己在中转站的余额、以及这个 Key 的额度——"
                    "确实不足的话，充值后重试。"
                    "如果你那边余额正常，那这句话多半是中转站转述它**上游渠道**的报错"
                    "（它自己接 OpenAI 的账户欠费了）——这种情况改本项目的任何配置都没用，"
                    "只能把上面这句原文发给中转站客服。"
                ),
            )
        if "content" in low and ("policy" in low or "safety" in low or "rejected" in low):
            # 图片模型的审核比文本模型严得多，这条会真的遇到
            return ImageGenerationResult(
                error="image_provider_content_rejected",
                message="生图服务认为这条提示词不合适，已拒绝生成。可以改一下画面描述再试。",
            )
        if code in ("invalid_api_key", "unauthorized", "authentication_error"):
            return ImageGenerationResult(
                error="image_provider_unauthorized",
                message="生图服务的密钥无效，请检查 .env 里的 IMAGE_API_KEY。",
            )

        return ImageGenerationResult(
            error="image_provider_error",
            message=f"生图服务返回错误：{detail or '未提供原因'}",
        )


class NullImageProvider:
    """兜底 Provider：没配置生图服务时使用。

    和 NullSearchProvider 一样，它不抛异常，只是老实说"我没配"，
    这样错误信息能一路传到前端，用户看到的是"IMAGE_API_KEY 没填"，
    而不是一句没有信息量的"生成失败"。
    """

    name = "none"

    def __init__(self, message: str = "") -> None:
        self.message = message

    def generate(self, prompt: str) -> ImageGenerationResult:
        return ImageGenerationResult(
            error="image_provider_not_configured",
            message=(
                self.message
                or "系统没有配置生图服务（IMAGE_API_KEY 或 IMAGE_BASE_URL 为空），"
                "无法生成配图。请先在服务器根目录的 .env 里填好这几项并重启后端。"
            ),
        )


class PersonalImageProvider(OpenAICompatibleImageProvider):
    """Personal endpoints and their returned image URLs are untrusted destinations."""
    def _client(self):
        return public_client(self.timeout)

    def generate(self, prompt):
        try:
            with self._client() as client, client.stream('POST', self.request_url,
                    json=self._build_payload(prompt), headers={'Authorization': f'Bearer {self.api_key}'}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES * 2:
                        raise ValueError('Response too large')
                    chunks.append(chunk)
            body = json.loads(b''.join(chunks))
            first = body.get('data', [])[0]
            if not isinstance(first, dict) or not (first.get('b64_json') or first.get('url')):
                raise ValueError('Missing image')
            result = self._extract_image(body)
        except Exception:
            return ImageGenerationResult(error='image_provider_error', message='个人图片 API 调用失败，请检查设置中的连接信息。')
        if not result.ok:
            # Providers may echo request headers/keys in errors; never persist or expose them.
            return ImageGenerationResult(error='image_provider_error', message='个人图片 API 未能生成图片，请核对密钥、模型权限、余额及接口地址。')
        if len(result.data) > MAX_DOWNLOAD_BYTES:
            return ImageGenerationResult(error='image_provider_bad_response', message='生成图片超过大小限制。')
        result.revised_prompt = result.revised_prompt.replace(self.api_key, '[redacted]')
        return result

    def _download(self, url, revised):
        try:
            with self._client() as client, client.stream('GET', url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError('Image too large')
                    chunks.append(chunk)
            return ImageGenerationResult(data=b''.join(chunks), revised_prompt=revised)
        except Exception:
            return ImageGenerationResult(error='image_provider_bad_response', message='图片地址不可用或不是允许的公网 HTTPS 地址。')


def get_image_model_name(user=None):
    connection = personal_connection(user, 'image', check_destination=False)
    return connection.model_name if connection else settings.IMAGE_MODEL


def get_image_provider(user=None) -> ImageProvider:
    """根据 .env 配置返回可用的生图 Provider。"""
    connection = personal_connection(user, 'image')
    if connection:
        return PersonalImageProvider(api_key=connection.api_key, base_url=connection.base_url, model=connection.model_name,
                                     timeout=settings.IMAGE_TIMEOUT, size=settings.IMAGE_SIZE,
                                     quality=settings.IMAGE_QUALITY, output_format=settings.IMAGE_OUTPUT_FORMAT)
    api_key = (settings.IMAGE_API_KEY or "").strip()
    base_url = (settings.IMAGE_BASE_URL or "").strip()
    model = (settings.IMAGE_MODEL or "").strip()

    # 三样缺一不可。中转站的地址每家都不同，没法给一个有意义的默认值 ——
    # 所以 base_url 为空时**不**回退到 OpenAI 官方地址，那只会让人更迷惑。
    if not api_key or not base_url or not model:
        missing = [
            name
            for name, value in (
                ("IMAGE_API_KEY", api_key),
                ("IMAGE_BASE_URL", base_url),
                ("IMAGE_MODEL", model),
            )
            if not value
        ]
        logger.info("未配置生图服务，缺少: %s", ", ".join(missing))
        return NullImageProvider(
            message=(
                f"生图服务配置不完整，缺少：{'、'.join(missing)}。"
                "请在服务器根目录的 .env 里补齐，然后 docker compose up -d backend。"
            )
        )

    return OpenAICompatibleImageProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=settings.IMAGE_TIMEOUT,
        size=settings.IMAGE_SIZE,
        quality=settings.IMAGE_QUALITY,
        output_format=settings.IMAGE_OUTPUT_FORMAT,
    )
