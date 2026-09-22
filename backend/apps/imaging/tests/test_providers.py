"""生图 Provider 的错误翻译。

为什么单独测这一层？
错误翻译是**唯一**能把"你自己余额不足"和"中转站上游欠费"区分开的地方。
翻错了，用户就会一直去后台充钱、反复检查 Key，永远查不到真正的原因 ——
所以每一条翻译都值得有个用例钉住。
"""

from __future__ import annotations

from typing import Any

import httpx

from apps.imaging.providers import OpenAICompatibleImageProvider


def _provider(model: str = "gpt-image-2") -> OpenAICompatibleImageProvider:
    return OpenAICompatibleImageProvider(
        api_key="sk-test",
        base_url="https://relay.example.com/v1",
        model=model,
        timeout=5,
        size="1536x1024",
        quality="high",
        output_format="jpeg",
    )


def _patch_httpx(mocker: Any, status: int, body: dict) -> None:
    """把 provider 内部用的 httpx.Client 换成"按剧本应答"的假客户端。

    和 apps/agent/tests/test_tools.py 里那套是同一个套路：
    先抓住真的 Client，再用 MockTransport 塞一个只会返回固定响应的传输层。
    """
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    mocker.patch(
        "apps.imaging.providers.httpx.Client",
        side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


class TestHttpErrorTranslation:
    def test_403_insufficient_balance_keeps_upstream_wording(self, mocker):
        """★ 回归用例：403 + 上游欠费，必须把上游原话透出来。

        中转站普遍是二层转售 —— 它自己接 OpenAI 的那条线路欠费时，会把上游的
        原话原样转给用户，看起来和"你欠费了"一模一样。

        以前 403 不走 body 翻译（只认 400/404），detail 明明解析出来了却被丢掉，
        用户只看到一句"HTTP 403"，然后去充自己的钱 —— 方向全错。
        """
        _patch_httpx(
            mocker,
            403,
            {
                "error": {
                    "message": "Insufficient account balance",
                    "type": "bad_response_status_code",
                    "code": "bad_response_status_code",
                }
            },
        )

        result = _provider().generate("a cat")

        assert result.error == "image_provider_quota_exceeded"
        # 原话要带出来，用户才能拿它去问中转站客服
        assert "Insufficient account balance" in result.message
        # 并且必须点明"可能是上游"，否则用户还是会以为是自己欠费
        assert "上游" in result.message

    def test_403_permission_error_is_not_reported_as_balance(self, mocker):
        """403 不都是余额问题 —— 中转站的"分组无权限"也走 403。

        把它翻译成"去充值"就是把用户往错误方向指，所以只保留原文、不硬套结论。
        """
        _patch_httpx(
            mocker,
            403,
            {"error": {"message": "无权访问 gpt pro 分组", "type": "new_api_error", "code": ""}},
        )

        result = _provider().generate("a cat")

        assert result.error != "image_provider_quota_exceeded"
        assert "无权访问 gpt pro 分组" in result.message

    def test_401_still_reports_bad_key(self, mocker):
        """401 的既有行为不能被 403 的改动带偏（401 分支排在前面）。"""
        _patch_httpx(
            mocker, 401, {"error": {"message": "invalid api key", "code": "invalid_api_key"}}
        )

        result = _provider().generate("a cat")

        assert result.error == "image_provider_unauthorized"
        assert "IMAGE_API_KEY" in result.message

    def test_400_bad_model_names_the_env_var(self, mocker):
        """填错 IMAGE_MODEL 是最常见的一类，必须指名道姓说该改哪个变量。"""
        _patch_httpx(
            mocker,
            400,
            {"error": {"message": "The model `gpt-image-9` does not exist", "code": ""}},
        )

        result = _provider("gpt-image-9").generate("a cat")

        assert result.error == "image_provider_bad_model"
        assert "IMAGE_MODEL" in result.message
