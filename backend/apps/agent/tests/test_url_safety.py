"""
URL 安全检查的测试。

这是整个项目最重要的安全测试 —— 如果这里出错，
别人就能让我们的服务器去访问内网、读取云服务器的密钥。
"""

from __future__ import annotations

import socket

import pytest

from apps.agent.tools.url_safety import validate_public_url
from apps.common.exceptions import UnsafeUrlError


@pytest.fixture(autouse=True)
def fake_dns(mocker):
    """把所有域名解析固定成一个公网 IP。

    ★ 这个 fixture 不是可有可无的。没有它，"应该放行"的用例会**真的去查 DNS**，
    于是测试结果取决于这台机器当时的网络环境：

      本机开着代理 / VPN 时（sing-box、Hiddify 这类默认用 198.18.0.0/15 做 fake-IP），
      域名会被解析到 198.18.x.x —— 安全检查**正确地**把它判成非公网地址，
      于是这些用例就莫名其妙地失败。看着像代码坏了，其实是环境变了。

    需要模拟"解析到内网"的用例，在测试体内自己再 patch 一次把它盖掉即可。
    """
    fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)


class TestSchemeValidation:
    """只允许 http / https。"""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "file://C:/Windows/win.ini",
            "ftp://example.com/file.txt",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
            "gopher://example.com",
            "ws://example.com",
        ],
    )
    def test_rejects_non_http_schemes(self, url):
        """各种非 http/https 协议都必须被拒绝。"""
        with pytest.raises(UnsafeUrlError):
            validate_public_url(url)

    def test_rejects_empty_and_garbage(self):
        for bad in ("", "   ", "not-a-url", "://missing-scheme"):
            with pytest.raises(UnsafeUrlError):
                validate_public_url(bad)

    def test_rejects_overlong_url(self):
        with pytest.raises(UnsafeUrlError):
            validate_public_url("https://example.com/" + "a" * 3000)


class TestLocalhostBlocking:
    """localhost 系列必须被拒绝。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/admin",
            "http://localhost:8000/api/v1/articles",
            "http://LOCALHOST/",
            "http://localhost.localdomain/",
            "http://something.local/",
            "http://db.internal/",
        ],
    )
    def test_blocks_localhost(self, url):
        with pytest.raises(UnsafeUrlError):
            validate_public_url(url)


class TestPrivateIpBlocking:
    """私有 / 回环 / 链路本地 IP 必须被拒绝。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://127.0.0.1:8000/",
            "http://0.0.0.0/",
            "http://10.0.0.1/",
            "http://10.255.255.254/",
            "http://172.16.0.1/",
            "http://172.31.255.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",  # 云厂商元数据地址
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6 绕过尝试
        ],
    )
    def test_blocks_private_ips(self, url):
        with pytest.raises(UnsafeUrlError):
            validate_public_url(url)


class TestPublicUrlAllowed:
    """正常的公网地址应该放行。"""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.example.com/article",
            "http://example.com/",
            "https://news.ycombinator.com/item?id=123",
        ],
    )
    def test_allows_public_urls(self, url):
        result = validate_public_url(url)
        assert result.startswith("http")

    def test_strips_credentials_and_fragment(self):
        """URL 里的用户名密码要去掉，防止被当成合法凭据传给目标站点。"""
        result = validate_public_url("https://user:pass@www.example.com/path#section")
        assert "user" not in result
        assert "pass" not in result
        assert "#" not in result

    def test_normalizes_path(self):
        result = validate_public_url("https://www.example.com/")
        assert result == "https://www.example.com/"


class TestDnsResolutionCheck:
    """域名解析后仍然要检查 IP —— 光看域名是防不住 SSRF 的。"""

    def test_blocks_domain_resolving_to_private_ip(self, mocker):
        """模拟一个"域名看起来正常，但解析到内网"的攻击场景。"""
        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.100", 0)),
        ]
        mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)

        with pytest.raises(UnsafeUrlError):
            validate_public_url("https://evil-looking-domain.com/")

    def test_blocks_proxy_fake_ip_range(self, mocker):
        """代理软件的 fake-IP 段（198.18.0.0/15）也要拦。

        这不是假想的情况：sing-box / Hiddify 这类工具默认就用这一段做 fake-IP。
        如果放行，等于把"本机开着代理"变成了一个绕过网段检查的口子。
        """
        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.12", 0)),
        ]
        mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)

        with pytest.raises(UnsafeUrlError):
            validate_public_url("https://www.example.com/article")

    def test_blocks_domain_resolving_to_metadata_ip(self, mocker):
        """解析到 169.254.169.254（云元数据）必须拒绝。"""
        import socket

        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ]
        mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)

        with pytest.raises(UnsafeUrlError):
            validate_public_url("https://metadata-proxy.com/")

    def test_allows_domain_resolving_to_public_ip(self, mocker):
        import socket

        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        mocker.patch("apps.agent.tools.url_safety.socket.getaddrinfo", return_value=fake_infos)

        assert validate_public_url("https://example.com/")

    def test_blocks_when_dns_fails(self, mocker):
        """解析不出来就拒绝 —— 宁可错杀，不可放过。"""
        import socket

        mocker.patch(
            "apps.agent.tools.url_safety.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        )
        with pytest.raises(UnsafeUrlError):
            validate_public_url("https://this-domain-does-not-exist-xyz123.com/")
