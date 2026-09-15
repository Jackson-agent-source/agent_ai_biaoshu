"""
pytest 全局 fixture。

放这里的东西所有测试都能直接用，例如：
    def test_xxx(api_client, user):
        ...
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture(autouse=True)
def reset_throttle_cache():
    """每个测试开始前清空缓存。

    ★ 这个 fixture 不是可有可无的。

    DRF 的频率限流把计数器存在 Django 的默认缓存里，而测试进程里用的是
    LocMemCache —— 它是**进程级**的，不会在测试之间自动清理。
    后果是：跑完整套测试时，前面几十个测试打过的请求会被算进后面测试的额度里，
    于是某个"应该成功"的用例突然收到 429（Too Many Requests）。

    这种失败最坑的地方在于**它取决于测试的执行顺序**：
    单独跑一个文件是绿的，跑全套就红了，而且报错信息完全指不到限流上。

    Django 自己的 TestCase 会清数据库，但不会碰缓存，所以必须手工处理。
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def isolated_media_root(tmp_path, settings):
    """每个测试用一个独立的临时目录当 MEDIA_ROOT。

    ★ 为什么必须 autouse（而不是只在配图测试里按需用）：
      配图是 FileField，任何**跑到生成配图**的测试都会真的往磁盘写文件。
      将来某个 Agent 测试顺手建一张 GeneratedImage，就会写进
      settings_test.py 里那个 session 级目录 —— 那还好；
      真正危险的是万一哪天那个覆盖被改掉或被绕过，写进去的就是**真实的
      backend/media/**，而且是完全静默的（.gitignore 把它挡得严严实实，
      不报错、没人发现）。

    ★ 顺带解决用例之间的互相干扰：测「删记录要连文件一起删」的时候，
      如果所有用例共用一个目录，断言"目录空了"就会看到别人留下的文件。

    ★ 不需要手工恢复 MEDIA_ROOT —— settings fixture（pytest-django 提供）
      会在用例结束后把改动还原。

    ★★ 这里有个必须实测才敢信的细节：Django 的 FileSystemStorage 把
       location 缓存成了 cached_property，所以"改了 settings 但 storage 还用
       旧路径"是这类 fixture 最经典的暗坑 —— 表现是**断言全过，但文件仍然
       落在真实 media/ 里**。已经确认 Django 5.2.17 的 FileSystemStorage
       监听了 setting_changed 信号、缓存会被一起清掉，所以这个写法是有效的。
       如果以后升级 Django 后有人发现 media/ 又开始变脏，先来验证这一条。
    """
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def user(db):
    """一个普通测试用户。"""
    return User.objects.create_user(username="tester", password="test-password-123")


@pytest.fixture
def other_user(db):
    """另一个用户，用来测试"不能访问别人的数据"。"""
    return User.objects.create_user(username="intruder", password="test-password-123")


@pytest.fixture
def api_client() -> APIClient:
    """未登录的接口客户端。"""
    return APIClient()


@pytest.fixture
def auth_client(user) -> APIClient:
    """已登录的接口客户端（用 DRF Token 认证）。"""
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def other_auth_client(other_user) -> APIClient:
    """另一个用户的已登录客户端。"""
    token, _ = Token.objects.get_or_create(user=other_user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client
