"""
请求 ID 中间件。

给每个进来的 HTTP 请求分配一个短 ID，作用：
1. 写进日志，一次请求的所有日志都能串起来；
2. 出错时返回给前端，用户截图给我们，我们就能在日志里精确定位。

日志示例：
    [request_id=3f2a1b9c] AgentRun 创建成功 ...
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# 用 ContextVar 而不是全局变量：在异步/多线程环境下也不会串号
_current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")


def get_current_request_id() -> str:
    """获取当前请求的 request_id；不在请求上下文中时返回空字符串。"""
    return _current_request_id.get()


def short_request_id() -> str:
    """生成一个 8 位短 ID，比完整 UUID 好读。"""
    return uuid.uuid4().hex[:8]


class RequestIDMiddleware:
    """Django 中间件：为每个请求写入 request_id。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 优先复用前端/网关传过来的 ID，方便跨服务追踪
        request_id = request.headers.get("X-Request-ID") or short_request_id()

        request.request_id = request_id
        token = _current_request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _current_request_id.reset(token)

        response["X-Request-ID"] = request_id
        return response
