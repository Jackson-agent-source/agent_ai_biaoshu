"""Credential storage and destination validation shared by both model factories."""
import base64
import hashlib
import ipaddress
import socket
import httpx
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import ValidationError

from apps.accounts.models import ModelConnection
from apps.common.exceptions import ModelProviderError


def _cipher():
    secret = getattr(settings, 'MODEL_CREDENTIAL_SECRET', '') or settings.SECRET_KEY
    key = hashlib.sha256(('model-credentials-v1:' + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_key(value):
    return _cipher().encrypt(value.encode()).decode()


def decrypt_key(value):
    try:
        return _cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        raise ModelProviderError('已保存的 API 密钥无法解密，请在设置中重新填写。') from None


def validate_base_url(value, *, resolve=False):
    value = value.strip().rstrip('/')
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValidationError('API 地址格式不正确。') from None
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or port not in (None, 443)
            or '\\' in value or any(ord(char) < 33 for char in value)):
        raise ValidationError('请填写 HTTPS API 基地址，不要包含密钥、查询参数或接口名称；仅支持标准 443 端口。')
    if url.path.rstrip('/').endswith(('/chat/completions', '/images/generations', '/models')):
        raise ValidationError('请填写 API 基地址（例如 https://api.deepseek.com/v1），不用填写具体接口路径。')
    host = url.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValidationError('请使用公网模型服务地址。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValidationError('不能使用内网、回环或保留地址。')
    if resolve:
        public_address(host)
    return value


def public_address(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValidationError('无法解析 API 地址，请检查域名。') from None
    # TUN proxies commonly synthesize RFC 2544 addresses. Resolve these names
    # independently, then connect to the actual validated IP, never to fake/private IPs.
    fake_net = ipaddress.ip_network('198.18.0.0/15')
    if addresses and all(ipaddress.ip_address(item[4][0]) in fake_net for item in addresses):
        return _public_dns_address(host)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValidationError('API 地址必须解析到公网服务器。')
    return sorted(addresses, key=lambda item: item[0] != socket.AF_INET)[0][4][0]


def _public_dns_address(host):
    cache_key = 'model-public-dns:' + hashlib.sha256(host.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached and ipaddress.ip_address(cached).is_global:
        return cached
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get('https://cloudflare-dns.com/dns-query', params={'name': host, 'type': 'A'},
                                  headers={'accept': 'application/dns-json'})
            response.raise_for_status()
            answers = [ipaddress.ip_address(item['data']) for item in response.json().get('Answer', []) if item.get('type') == 1]
        if not answers or not all(address.is_global for address in answers):
            raise ValueError('Not public')
        value = str(answers[0])
        cache.set(cache_key, value, 60)
        return value
    except Exception:
        raise ValidationError('本机代理返回了虚拟地址，且无法核验公网 DNS；请检查代理或 DNS 设置。') from None


@dataclass(frozen=True)
class Connection:
    base_url: str
    model_name: str
    api_key: str = field(repr=False)


def personal_connection(user, kind, *, check_destination=True):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    record = ModelConnection.objects.filter(user=user, kind=kind, enabled=True).first()
    if not record:
        return None
    if not record.encrypted_key or not record.base_url or not record.model_name:
        raise ModelProviderError('个人 API 配置不完整，请在设置中补齐，或切换为服务器默认配置。')
    if check_destination:
        validate_base_url(record.base_url, resolve=True)
    return Connection(record.base_url, record.model_name, decrypt_key(record.encrypted_key))


def public_connection(user, kind):
    record = ModelConnection.objects.filter(user=user, kind=kind).first()
    prefix = 'LLM' if kind == 'text' else 'IMAGE'
    return {
        'kind': kind, 'enabled': bool(record and record.enabled),
        'base_url': record.base_url if record else '',
        'model_name': record.model_name if record else '',
        'has_key': bool(record and record.encrypted_key),
        'default_available': bool(getattr(settings, prefix + '_API_KEY', '') and getattr(settings, prefix + '_MODEL', '')),
        'default_model': getattr(settings, prefix + '_MODEL', '') or '',
    }
