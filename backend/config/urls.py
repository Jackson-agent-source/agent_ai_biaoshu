"""项目根路由。

所有业务接口统一挂在 /api/v1/ 下面：
    /api/v1/auth/...       登录注册
    /api/v1/agent/...      Agent 运行、工具调用记录、来源
    /api/v1/articles/...   文章（含配图、公众号推送）
    /media/...             配图文件（生产由 nginx 直接发，开发由 Django 发）
    /admin/                Django 自带的数据库管理后台
"""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.articles.views import ArticleListView

# 网站前端的地址，定义在 config/settings.py 里（SITE_ORIGIN）。
# 根路径那几句提示语靠它来生成，免得写死 localhost 之后部署上线了还在指本机。
SITE_ORIGIN = settings.SITE_ORIGIN


def health_check(_request):
    """最简单的存活探测接口，前端可以用来判断后端是否启动。

    ★ Docker 的 healthcheck 也打这个地址（走 127.0.0.1:8000），
      所以 .env 里的 ALLOWED_HOSTS 必须保留 127.0.0.1，
      否则它会一直返回 400，容器永远是 unhealthy。
    """
    return JsonResponse({"success": True, "data": {"status": "ok"}, "message": ""})


def root(_request):
    """根路径给一句人话，而不是一个裸的 404。

    这一条纯粹是为了少踩坑：后端只提供 API，本身没有网页。
    但 8000 这个地址太顺手了，几乎每个人第一次都会直接开它，
    然后看到一个 Django 调试页面的 404，以为是项目跑挂了。
    """
    # 这里用 JsonResponse 而不是 DRF 的 success_response()：
    # 普通 Django 视图没有经过 APIView.dispatch，DRF 的 Response 拿不到
    # accepted_renderer，.render() 会直接抛 "Accepted renderer must be set"。
    # 信封形状和别处保持一致，只是自己拼一下。
    # json_dumps_params 是为了让中文别被转义成 \uXXXX —— 这个接口是给人看的。
    return JsonResponse(
        {
            "success": True,
            "data": {
                "service": "微信公众号 AI Content Agent —— 后端 API",
                "frontend": SITE_ORIGIN,
                "health": "/health/",
                "admin": f"/{settings.ADMIN_URL_PATH}",
                "api": "/api/v1/",
            },
            "message": f"这是后端 API，不是网站页面。网站请开 {SITE_ORIGIN}",
        },
        json_dumps_params={"ensure_ascii": False},
    )


urlpatterns = [
    path("", root, name="root"),
    # ★ 后台地址来自环境变量。默认仍是 admin/（本地开发不用改），
    #   放到公网时在 .env 里改成别猜不到的路径，例如 ADMIN_URL_PATH=manage-8f3a91/
    #   （结尾要带斜杠）。改了之后 README 里提到后台地址的地方也要同步改。
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path("health/", health_check, name="health-check"),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/agent/", include("apps.agent.urls")),
    # ★ 这一行是故意加的，不是重复配置。
    #
    # 本项目的接口约定是"集合地址不带末尾斜杠"，和 agent 那边保持一致：
    #     /api/v1/agent/runs
    #     /api/v1/agent/conversations
    #     /api/v1/articles        ← 就是这个
    #
    # 但 articles 这个 include 挂在 "api/v1/articles/" 上，
    # 里面的 path("") 只能匹配到**带斜杠**的 /api/v1/articles/。
    # 前端请求不带斜杠时，Django 的 APPEND_SLASH 会返回 301 跳转：
    #     GET  -> 多一次往返，能跑通，但慢
    #     POST -> 某些客户端不会带着请求体跟着跳，直接失败
    #
    # 所以这里显式把不带斜杠的地址也指到同一个 View，让两种写法都能用。
    path("api/v1/articles", ArticleListView.as_view(), name="article-list-no-slash"),
    path("api/v1/articles/", include("apps.articles.urls")),
    # 配图与公众号推送。两个 app 的 url 里写的是相对路径，
    # 都挂在 api/v1/ 下面，各自的前缀（articles/... / images/... /
    # wechat-publishes/...）互不冲突。
    path("api/v1/", include("apps.imaging.urls")),
    path("api/v1/", include("apps.wechat.urls")),
]

# ★ 本地开发时由 Django 自己把配图发出去。
#
#   生产环境走的是 nginx（frontend/nginx.conf 里的 location ^~ /media/），
#   请求根本进不到 Django；但本地开发没有 nginx，不加这一段的话
#   <img src="/media/..."> 会 404。
#
#   static() 在 DEBUG=False 时本来就返回空列表，这里显式 if 一遍
#   只是为了让"只在开发环境生效"这件事一眼可见。
if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
