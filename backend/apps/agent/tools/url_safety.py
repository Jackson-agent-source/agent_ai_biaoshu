"""
URL 安全校验 —— 防止 SSRF（服务端请求伪造）。

要防的问题：
    大模型（或者搜索结果）可能给出 http://127.0.0.1:8000/admin 这样的地址。
    如果我们的服务器老老实实去请求它，就等于让外部的人借我们的服务器
    去访问内网、云厂商的元数据接口（169.254.169.254）等等。
    这类攻击叫 SSRF。

我们的防线（三道）：
    1. 协议白名单：只允许 http / https，禁掉 file:// ftp:// data:// 等。
    2. 主机名黑名单：localhost、*.local、*.internal、云元数据域名。
    3. 解析后的 IP 检查：把域名解析成 IP，逐个检查是不是私有/回环/链路本地地址。
       这一步很关键 —— 光看域名不够，因为 evil.com 完全可以解析到 192.168.1.1。

残留风险（诚实说明）：
    第 3 道防线存在"DNS Rebinding"的理论风险 —— 我们校验时解析到公网 IP，
    但真正发起请求时 DNS 又变回内网 IP。
    彻底解决需要把校验通过的 IP 固定下来直接连（HTTP 客户端要支持）。
    V1 作为本地单人使用的工具，三道防线已经足够，这里明确记录该限制。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse, urlunparse

from apps.common.exceptions import UnsafeUrlError

logger = logging.getLogger(__name__)

# 只允许这两种协议
ALLOWED_SCHEMES = {"http", "https"}

# 明确禁止的主机名
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    # 云厂商元数据服务（AWS / GCP / 阿里云等），泄露凭据的重灾区
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

# 禁止的域名后缀（内网常见命名）
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa", ".lan")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断一个 IP 是否属于"不该被外部访问"的地址段。

    覆盖：私有网段、回环、链路本地、保留、组播、未指定、以及 IPv4 映射的 IPv6。
    """
    # ::ffff:127.0.0.1 这种 IPv4-mapped IPv6 地址要先还原成 IPv4 再判断
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    return (
        ip.is_private          # 10.x / 172.16-31.x / 192.168.x / fc00::/7
        or ip.is_loopback      # 127.x / ::1
        or ip.is_link_local    # 169.254.x / fe80::
        or ip.is_reserved      # 保留段
        or ip.is_multicast     # 组播
        or ip.is_unspecified   # 0.0.0.0
    )


def validate_public_url(url: str) -> str:
    """校验 URL 是否安全，返回规范化后的 URL；不安全则抛 UnsafeUrlError。

    这是 read_webpage 的第一道关卡，任何要发起网络请求的地方都应该先调用它。
    """
    if not url or not isinstance(url, str):
        raise UnsafeUrlError("链接为空或格式不正确。")

    url = url.strip()

    # 长度限制，避免超长 URL 拖垮解析
    if len(url) > 2000:
        raise UnsafeUrlError("链接过长，已拒绝访问。")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise UnsafeUrlError("链接格式无法解析。") from exc

    # --- 第 1 道防线：协议 ---
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"只允许访问 http 或 https 链接，当前协议是「{scheme or '空'}」。"
        )

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        raise UnsafeUrlError("链接里没有有效的主机名。")

    # --- 第 2 道防线：主机名 ---
    if hostname in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError("不允许访问本机或内网地址。")
    if hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("不允许访问内网域名。")

    # --- 第 3 道防线：IP 检查 ---
    # 3a. 如果主机名本身就是 IP 字面量，直接判断
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_blocked_ip(literal_ip):
            raise UnsafeUrlError("不允许访问内网 IP 地址。")
    else:
        # 3b. 是域名，解析成 IP 再逐个检查
        for ip in _resolve_hostname(hostname):
            if _is_blocked_ip(ip):
                logger.warning("域名 %s 解析到内网地址 %s，已拒绝", hostname, ip)
                raise UnsafeUrlError("该域名指向内网地址，不允许访问。")

    # --- 规范化：去掉 URL 里的用户名密码（http://user:pass@host/ 这种） ---
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    return urlunparse((scheme, netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def _resolve_hostname(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """把域名解析成 IP 列表。

    解析失败时抛 UnsafeUrlError —— 宁可拒绝，也不要放行一个无法验证的地址。
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"域名 {hostname} 无法解析。") from exc
    except Exception as exc:  # pragma: no cover
        raise UnsafeUrlError("域名解析失败。") from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        try:
            ips.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:  # pragma: no cover
            continue

    if not ips:
        raise UnsafeUrlError("域名没有解析出任何 IP 地址。")
    return ips
