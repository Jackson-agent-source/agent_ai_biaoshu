"""
账号 API。

认证方案：DRF 的 Token 认证。
    前端登录后拿到一个 token，存在 localStorage 里，
    之后每个请求都带上请求头：Authorization: Token <token>

为什么不用 Session + CSRF？
    Token 对前端更简单（不用处理 CSRF cookie），也更适合以后前后端分开部署。
    而且 Token 是 DRF 官方组件，不算"自己造认证系统"。
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import (
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.common.exceptions import AppError, RegistrationClosedError
from apps.common.responses import success_response

logger = logging.getLogger(__name__)


class LoginView(APIView):
    """POST /api/v1/auth/login"""

    permission_classes = [AllowAny]
    # ★ 防撞库。限流规则在 settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"]，
    #   默认 20/min，可用 .env 里的 THROTTLE_LOGIN 调整。
    throttle_scope = "login"

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            # 安全习惯：不告诉对方"是用户名错还是密码错"
            logger.info("登录失败 username=%s", serializer.validated_data["username"])
            # 用 400 而不是 401：401 会让前端误以为"登录过期"而跳转登录页，
            # 但用户其实正在登录页上，只是密码打错了。
            raise AppError("用户名或密码不正确。", code="INVALID_CREDENTIALS")

        if not user.is_active:
            raise AppError("该账号已被禁用。", code="ACCOUNT_DISABLED")

        # 一个用户一个 token，重复登录就复用（也可以每次新建，V1 选简单方案）
        token, _created = Token.objects.get_or_create(user=user)
        logger.info("登录成功 user_id=%s", user.id)

        return success_response(
            {
                "token": token.key,
                "user": UserSerializer(user).data,
            }
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout —— 把 token 删掉，前端也要清掉本地的。"""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        Token.objects.filter(user=request.user).delete()
        logger.info("登出 user_id=%s", request.user.id)
        return success_response(None, message="已退出登录。")


class MeView(APIView):
    """GET /api/v1/auth/me —— 前端用来判断"我登录了吗"。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return success_response(UserSerializer(request.user).data)


class RegisterView(APIView):
    """POST /api/v1/auth/register

    V1 为了方便本地测试保留了注册接口。
    ★ 放到公网时请把 .env 里的 ALLOW_REGISTRATION 改成 False ——
      否则任何陌生人注册之后就能用你的 LLM_API_KEY 生成文章，这钱是你出的。
      关掉之后账号由管理员在后台创建（见 README 的「部署」一章）。
    """

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        # ★ 这个开关是本次部署新增的，本地开发默认仍是 True（行为不变）
        if not settings.ALLOW_REGISTRATION:
            logger.info("拒绝注册请求：自助注册已关闭")
            raise RegistrationClosedError()

        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        token, _created = Token.objects.get_or_create(user=user)
        logger.info("注册成功 user_id=%s", user.id)

        return success_response(
            {"token": token.key, "user": UserSerializer(user).data},
            message="注册成功。",
            status_code=status.HTTP_201_CREATED,
        )
