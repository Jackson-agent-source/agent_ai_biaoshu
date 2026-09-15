"""
统一异常体系 + DRF 全局异常处理器。

为什么要这样做？
- 前端只需要认一种返回格式：{"success": false, "error": {"code", "message", "request_id"}}
- 用户永远看不到 Python Traceback / 数据库密码 / 文件路径
- 完整异常仍然会写进服务器日志，方便我们自己排查

所有自定义异常都继承 AppError，带上三样东西：
    code        机器可读的错误码，前端可以据此做分支
    message     给用户看的中文提示
    status_code HTTP 状态码
"""

from __future__ import annotations

import logging
from typing import Any

from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 基础异常
# ---------------------------------------------------------------------------
class AppError(Exception):
    """本项目所有业务异常的基类。"""

    code: str = "APP_ERROR"
    message: str = "服务出现异常，请稍后重试。"
    status_code: int = http_status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None) -> None:
        if message:
            self.message = message
        if code:
            self.code = code
        self.details = details
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Agent 相关异常
# ---------------------------------------------------------------------------
class AgentExecutionError(AppError):
    """Agent 整体执行失败（模型报错、流程中断等）。"""

    code = "AGENT_EXECUTION_FAILED"
    message = "文章生成失败，请稍后重试。"
    status_code = http_status.HTTP_502_BAD_GATEWAY


class ModelProviderError(AppError):
    """大模型服务调用失败：Key 不对、余额不足、网络不通、超时。"""

    code = "MODEL_PROVIDER_ERROR"
    message = "大模型服务暂时不可用，请检查 API Key 与网络后重试。"
    status_code = http_status.HTTP_502_BAD_GATEWAY


class StructuredOutputError(AppError):
    """模型返回的内容不符合 ArticleDraft 结构。"""

    code = "STRUCTURED_OUTPUT_ERROR"
    message = "模型返回的文章结构不完整，请重试一次。"
    status_code = http_status.HTTP_502_BAD_GATEWAY


class AgentLimitExceededError(AppError):
    """Agent 工具调用次数超限。V1 不会直接抛给前端，而是转成 risk_note。"""

    code = "AGENT_LIMIT_EXCEEDED"
    message = "Agent 研究次数达到上限。"
    status_code = http_status.HTTP_429_TOO_MANY_REQUESTS


class DailyQuotaExceededError(AppError):
    """当天生成额度用完（单用户或全站）。这是防烧钱的最后一道闸。

    注意：status_code 是**类属性**，AppError.__init__ 只接受
    message / code / details 三个参数 —— 所以额度提示必须新开一个子类，
    不能写成 AppError("...", status_code=429)，那样会 TypeError。
    """

    code = "DAILY_QUOTA_EXCEEDED"
    message = "今天的生成额度已用完，请明天再试。"
    status_code = http_status.HTTP_429_TOO_MANY_REQUESTS


class RegistrationClosedError(AppError):
    """自助注册已关闭（部署到公网时的推荐配置）。"""

    code = "REGISTRATION_CLOSED"
    message = "本服务不开放自助注册，请联系管理员开通账号。"
    status_code = http_status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Tool 相关异常
# ---------------------------------------------------------------------------
class ToolError(AppError):
    """工具执行异常基类。工具内部一般不抛异常，而是返回 {"success": False, "error": ...}。"""

    code = "TOOL_ERROR"
    message = "工具执行失败。"
    status_code = http_status.HTTP_502_BAD_GATEWAY


class SearchProviderError(ToolError):
    """搜索服务失败/超时/未配置。"""

    code = "SEARCH_PROVIDER_ERROR"
    message = "联网搜索暂时不可用。"


class WebpageFetchError(ToolError):
    """网页抓取失败。"""

    code = "WEBPAGE_FETCH_ERROR"
    message = "网页内容读取失败。"


class UnsafeUrlError(WebpageFetchError):
    """URL 未通过安全检查（内网地址、非法协议等）。"""

    code = "UNSAFE_URL"
    message = "该链接不允许访问。"


# ---------------------------------------------------------------------------
# 配图相关异常
# ---------------------------------------------------------------------------
# ★ 注意：AppError.status_code 是**类属性**，不能靠构造函数传进去。
#   所以每一种 HTTP 状态码都必须新开一个子类，这是刻意的设计
#   （避免有人随手写出一个状态码和 code 对不上的异常）。
class ImageProviderError(ToolError):
    """生图服务失败/超时/未配置。"""

    code = "IMAGE_PROVIDER_ERROR"
    message = "配图生成服务暂时不可用。"


class ImageLimitExceededError(AppError):
    """单篇文章的配图数量到顶了。

    给 400 而不是 429：这不是"今天用完了、明天再来"，
    而是"这篇已经塞满了"，用户删一张就能继续，不需要等。
    """

    code = "IMAGE_LIMIT_EXCEEDED"
    message = "这篇文章的配图数量已达上限。"


class ImageQuotaExceededError(DailyQuotaExceededError):
    """配图每日额度用完。

    继承 DailyQuotaExceededError 而不是 AppError，是为了直接拿到 429 ——
    配图和文章生成在用户看来是同一类"额度用完，等明天"的情形，
    前端可以共用同一套提示。
    """

    code = "IMAGE_QUOTA_EXCEEDED"
    message = "今天的配图额度已经用完了，请明天再试。"


# ---------------------------------------------------------------------------
# 微信公众号相关异常
# ---------------------------------------------------------------------------
class WeChatNotConfiguredError(AppError):
    """没配置公众号凭据。

    这个是**用户自己能改的配置问题**，不是服务端故障，
    所以给 400 而不是 502 —— 502 会让用户以为"服务器坏了"然后去查服务器。
    """

    code = "WECHAT_NOT_CONFIGURED"
    message = "还没有配置微信公众号，请先在服务器的 .env 里填好 WECHAT_APP_ID 和 WECHAT_APP_SECRET。"


class WeChatPublishError(ToolError):
    """推送公众号失败（微信返回了错误码，或者文章本身不满足推送条件）。"""

    code = "WECHAT_PUBLISH_ERROR"
    message = "推送到公众号失败。"


# ---------------------------------------------------------------------------
# 来源校验异常
# ---------------------------------------------------------------------------
class SourceValidationError(AppError):
    """来源校验失败。V1 不直接抛给用户，而是丢弃来源 + 写 risk_note。"""

    code = "SOURCE_VALIDATION_ERROR"
    message = "部分来源未通过校验。"


# ---------------------------------------------------------------------------
# DRF 异常处理器
# ---------------------------------------------------------------------------
def _get_request_id(context: dict) -> str:
    """从请求对象里取出 request_id（由 RequestIDMiddleware 写入）。"""
    request = context.get("request")
    return getattr(request, "request_id", "") or "-"


def envelope_exception_handler(exc: Exception, context: dict) -> Response:
    """把所有异常统一转换成 {success: false, error: {...}} 信封。

    在 settings.REST_FRAMEWORK["EXCEPTION_HANDLER"] 里注册，全局生效。
    View 里不需要写 try/except，也不允许自己发明返回格式。
    """
    request_id = _get_request_id(context)

    # 1) 我们自己定义的业务异常 —— 信息是安全的，可以直接给用户看
    if isinstance(exc, AppError):
        logger.warning(
            "[request_id=%s] 业务异常 code=%s message=%s details=%s",
            request_id,
            exc.code,
            exc.message,
            exc.details,
        )
        return Response(
            {
                "success": False,
                "error": {"code": exc.code, "message": exc.message, "request_id": request_id},
            },
            status=exc.status_code,
        )

    # 2) 交给 DRF 处理参数校验错误、权限错误等
    response = drf_exception_handler(exc, context)

    if response is not None:
        code = "VALIDATION_ERROR" if response.status_code == http_status.HTTP_400_BAD_REQUEST else "REQUEST_ERROR"
        message = _flatten_drf_error(response.data)

        # 401 单独给一个前端好识别的 code，方便自动跳登录页
        if response.status_code == http_status.HTTP_401_UNAUTHORIZED:
            code, message = "UNAUTHENTICATED", "请先登录。"
        elif response.status_code == http_status.HTTP_403_FORBIDDEN:
            code, message = "PERMISSION_DENIED", "你没有权限访问该资源。"
        elif response.status_code == http_status.HTTP_404_NOT_FOUND:
            code, message = "NOT_FOUND", "请求的资源不存在。"
        elif response.status_code == http_status.HTTP_405_METHOD_NOT_ALLOWED:
            code, message = "METHOD_NOT_ALLOWED", "请求方法不被允许。"
        elif response.status_code == http_status.HTTP_429_TOO_MANY_REQUESTS:
            # ★ 必须单独处理 429。DRF 的 Throttled 异常默认文案是英文的
            #   （Throttled.default_detail = 'Request was throttled.'），
            #   不拦的话它会原样穿过这个处理器显示给用户 ——
            #   中文界面里蹦出一句英文，用户看不懂也不知道该干什么。
            code, message = "TOO_MANY_REQUESTS", "操作太频繁了，请稍后再试。"

        response.data = {
            "success": False,
            "error": {"code": code, "message": message, "request_id": request_id},
        }
        return response

    # 3) 完全没预料到的异常 —— 记录完整堆栈到服务器日志，但只给用户一句通用提示
    logger.exception("[request_id=%s] 未处理的服务端异常", request_id)
    return Response(
        {
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "服务器内部错误，请稍后重试。",
                "request_id": request_id,
            },
        },
        status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _flatten_drf_error(data: Any) -> str:
    """把 DRF 的嵌套错误字典压成一行中文提示，例如 {"topic": ["不能为空"]} -> "topic: 不能为空"。

    校验规则由 Serializer 写在中文 error_messages 里，所以这里直接展示最内层文本。
    """
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        for item in data:
            text = _flatten_drf_error(item)
            if text:
                return text
        return "请求参数不正确。"
    if isinstance(data, dict):
        # detail 是 DRF 常见的外层包装
        if "detail" in data:
            return _flatten_drf_error(data["detail"])
        parts: list[str] = []
        for field, value in data.items():
            text = _flatten_drf_error(value)
            if text:
                parts.append(f"{field}: {text}")
        return "；".join(parts) if parts else "请求参数不正确。"
    return "请求参数不正确。"
