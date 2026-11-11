"""
大模型工厂 —— 全项目**唯一**允许读取 API Key 的地方。

为什么要单独抽一层？
1. 密钥集中管理，不会散落在十几个文件里，也绝不会被写进代码。
2. 换模型/换厂商只改 .env，不改代码。
3. 测试时可以直接 monkeypatch 这个函数，注入假的模型对象，
   避免每次跑测试都真的花钱调用大模型。

其他文件请统一这样用：
    from apps.agent.services.model_factory import get_chat_model
    model = get_chat_model()
"""

from __future__ import annotations

import logging
import httpx
from apps.accounts.connections import personal_connection
from apps.accounts.transport import public_client, AsyncPublicTransport

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel

from apps.common.exceptions import ModelProviderError

logger = logging.getLogger(__name__)

# 这些 provider 走 OpenAI 兼容接口，直接用 ChatOpenAI，
# 这样才方便支持自定义的 LLM_BASE_URL（国内大部分服务都是这种）。
_OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai-compatible", "deepseek", "qwen", "moonshot", "zhipu", "ollama"}


def get_model_name(user=None) -> str:
    """返回当前配置的模型名，用于写进 AgentRun.model_name / Article.model_name。

    只返回模型名，绝不返回 Key。
    """
    connection = personal_connection(user, 'text', check_destination=False)
    return connection.model_name if connection else settings.LLM_MODEL or "未配置"


def is_model_configured(user=None) -> bool:
    """检查模型是否已经配置好。

    没配置时，接口应该给用户一句清楚的中文提示，
    而不是抛一个看不懂的 OpenAI SDK 报错。
    """
    return bool(personal_connection(user, 'text', check_destination=False) or (settings.LLM_MODEL and settings.LLM_API_KEY))


def get_model_provider(user=None):
    connection = personal_connection(user, 'text', check_destination=False)
    if connection:
        return 'deepseek' if 'deepseek' in connection.model_name.lower() else 'openai-compatible'
    return settings.LLM_PROVIDER.strip().lower()


def get_chat_model(
    *,
    user=None,
    temperature: float | None = None,
    model_name: str | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """创建并返回一个 LangChain ChatModel 实例。

    参数（全部可选，不传就用 .env 里的默认值）：
        temperature: 采样温度。写作建议 0.3~0.7：太低会死板，太高会跑题。
        model_name:  临时指定另一个模型名。
        max_tokens:  单次回复的最大 token 数。

    返回：
        BaseChatModel 实例，可以直接传给 LangChain 的 create_agent。

    异常：
        ModelProviderError —— 没有配置 Key 或模型名，或者 provider 不认识。
    """
    connection = personal_connection(user, 'text')
    if connection:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name or connection.model_name, api_key=connection.api_key, base_url=connection.base_url,
            temperature=settings.LLM_TEMPERATURE if temperature is None else temperature,
            timeout=settings.LLM_TIMEOUT, max_retries=settings.LLM_MAX_RETRIES,
            http_client=public_client(settings.LLM_TIMEOUT),
            http_async_client=httpx.AsyncClient(timeout=settings.LLM_TIMEOUT, follow_redirects=False,
                                               trust_env=False, transport=AsyncPublicTransport()),
            **({'max_tokens': max_tokens} if max_tokens else {}),
        )
    provider = (settings.LLM_PROVIDER or "openai").strip().lower()
    resolved_model = model_name or settings.LLM_MODEL
    resolved_temperature = settings.LLM_TEMPERATURE if temperature is None else temperature

    if not resolved_model:
        raise ModelProviderError("尚未配置文字模型，请在网页「设置」中填写模型名称和 API Key，或由管理员配置服务器默认模型。")

    if provider in _OPENAI_COMPATIBLE_PROVIDERS:
        return _build_openai_compatible_model(
            provider=provider,
            model_name=resolved_model,
            temperature=resolved_temperature,
            max_tokens=max_tokens,
        )

    # 其他 provider（比如 anthropic、google）交给 LangChain 官方的 init_chat_model
    return _build_via_init_chat_model(
        provider=provider,
        model_name=resolved_model,
        temperature=resolved_temperature,
        max_tokens=max_tokens,
    )


def _build_openai_compatible_model(
    *,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int | None,
) -> BaseChatModel:
    """构造 OpenAI 兼容协议的模型客户端。

    适用：OpenAI 官方、DeepSeek、通义千问、Kimi、智谱、本地 Ollama 等。
    区别只在 base_url 和 api_key，所以用同一套代码 + 环境变量区分。
    """
    # 延迟导入：没装 langchain-openai 时也不会影响其他功能
    from langchain_openai import ChatOpenAI

    api_key = settings.LLM_API_KEY
    base_url = settings.LLM_BASE_URL or None

    if provider == "ollama":
        # Ollama 本地服务不需要真实 Key，但 SDK 要求非空
        api_key = api_key or "ollama"
        base_url = base_url or "http://localhost:11434/v1"

    if not api_key:
        raise ModelProviderError(
            "尚未配置文字模型 API Key，请在网页「设置」中填写并启用个人 API，或由管理员配置服务器默认 LLM_API_KEY。"
        )

    kwargs: dict = {
        "model": model_name,
        "api_key": api_key,          # 只在内存中使用，不写日志、不入库
        "temperature": temperature,
        "timeout": settings.LLM_TIMEOUT,
        "max_retries": settings.LLM_MAX_RETRIES,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    logger.info(
        "创建模型客户端 provider=%s model=%s base_url=%s",
        provider,
        model_name,
        base_url or "(官方默认)",
    )
    return ChatOpenAI(**kwargs)


def _build_via_init_chat_model(
    *,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int | None,
) -> BaseChatModel:
    """使用 LangChain 官方的 init_chat_model 构造模型。

    非 OpenAI 兼容协议（例如 anthropic）走这里。
    """
    from langchain.chat_models import init_chat_model

    kwargs: dict = {"temperature": temperature}
    if settings.LLM_API_KEY:
        kwargs["api_key"] = settings.LLM_API_KEY
    if settings.LLM_BASE_URL:
        kwargs["base_url"] = settings.LLM_BASE_URL
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    logger.info("创建模型客户端 provider=%s model=%s (via init_chat_model)", provider, model_name)
    try:
        return init_chat_model(model=model_name, model_provider=provider, **kwargs)
    except Exception as exc:  # 例如 provider 名字写错
        logger.exception("init_chat_model 创建失败 provider=%s", provider)
        raise ModelProviderError(
            f"无法创建模型客户端，请检查 .env 里的 LLM_PROVIDER={provider} 是否正确。"
        ) from exc
