"""统一分页器。前端所有列表接口都用同一套分页参数与返回结构。"""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """?page=1&page_size=10

    page_size 上限 100，防止有人一次把整张表拉走。
    """

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100
