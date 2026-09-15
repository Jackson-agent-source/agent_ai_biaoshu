"""
统一 API 返回格式。

成功：
    {"success": true, "data": {...}, "message": ""}

失败：
    {"success": false, "error": {"code": "...", "message": "...", "request_id": "..."}}

失败信封由 apps/common/exceptions.py 里的全局异常处理器自动生成，
View 只需要调用这里的 success_response()。
"""

from __future__ import annotations

from typing import Any

from rest_framework import status as http_status
from rest_framework.response import Response


def success_response(
    data: Any = None,
    message: str = "",
    status_code: int = http_status.HTTP_200_OK,
) -> Response:
    """返回成功信封。

    用法：
        return success_response({"article_id": str(article.id)})
        return success_response(message="已保存", status_code=201)
    """
    return Response({"success": True, "data": data, "message": message}, status=status_code)


def error_response(
    code: str,
    message: str,
    status_code: int = http_status.HTTP_400_BAD_REQUEST,
    request_id: str = "",
) -> Response:
    """返回失败信封。

    一般不用手动调用 —— 抛 AppError 子类即可，异常处理器会转成这个格式。
    只有少数"非异常"的失败分支才需要直接调用。
    """
    return Response(
        {
            "success": False,
            "error": {"code": code, "message": message, "request_id": request_id or "-"},
        },
        status=status_code,
    )


def paginated_response(paginator, data: list[Any], message: str = "") -> Response:
    """把 DRF 分页器的结果包装成统一信封。

    前端拿到的 data 形状：
        {"count": 42, "page": 1, "page_size": 10, "total_pages": 5, "results": [...]}
    """
    return success_response(
        {
            "count": paginator.page.paginator.count,
            "page": paginator.page.number,
            "page_size": paginator.page.paginator.per_page,
            "total_pages": paginator.page.paginator.num_pages,
            "results": data,
        },
        message=message,
    )
