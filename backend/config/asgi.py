"""ASGI 入口。

V1 的 Agent 调用是同步的（前端发一个请求，一直等到文章生成完）。
V1.1 要做 SSE 流式状态推送时，才需要把这个入口跑起来：
    uvicorn config.asgi:application
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
