"""
测试专用配置。

作用：跑 pytest 时不受本地 .env 影响 —— 即使你把 .env 改成 MySQL，
测试也永远用 SQLite 内存数据库，速度快、不污染真实数据。

同时把 LLM / 搜索的调用全部换成假实现，测试绝不消耗你的 API 额度。

★ 从 .settings import * 里继承来的东西里有三个必须覆盖，否则测试会**污染真实数据**
  或者让测试结果依赖你本地的 .env：
    1. DATABASES           -> 换成 SQLite 内存库
    2. MEDIA_ROOT          -> 换成临时目录（见下面「媒体文件」那一节）
    3. IMAGE_* / WECHAT_*   -> 写死成假值，测试结果才不受本地 .env 影响
"""

import tempfile

from .settings import *  # noqa: F401,F403  （先加载正常配置，再覆盖）

# ---- 数据库：SQLite ----
# Django 跑测试时会自动使用内存数据库，测试结束后自动销毁
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ---- 测试时不需要真实密钥 ----
SECRET_KEY = "test-only-secret-key"
DEBUG = False

# 显式关掉 whitenoise。
# 这个文件是在 `from .settings import *` 之后才把 DEBUG 改成 False 的，
# 而 settings.py 里 STATICFILES_SERVE 早就按导入时的 DEBUG 算好了 ——
# 所以这里必须显式覆盖，否则行为取决于导入顺序，非常隐晦。
# 关掉它的额外好处：whitenoise 在 STATIC_ROOT 不存在时会每次请求打一条警告，
# 测试里会把有用的警告全埋掉。
STATICFILES_SERVE = False

# ---- 测试环境下的模型配置 ----
# 不配置真实 Key。测试里会用假的 Model 对象替换掉 get_chat_model()，
# 所以这里留空是安全的。
LLM_PROVIDER = "openai"
LLM_MODEL = "test-model"
LLM_API_KEY = "test-key-not-real"
LLM_BASE_URL = ""

SEARCH_PROVIDER = "tavily"
SEARCH_API_KEY = "test-search-key-not-real"

# ---- 媒体文件：绝对不要写进真实的 backend/media/ ----
# settings.py 里 MEDIA_ROOT = BASE_DIR / "media"，而生成配图的测试会**真的写文件**。
# 不覆盖的话，跑一次 pytest 就会在你本地的 backend/media/ 里留下一堆测试图，
# 而且它们被 .gitignore 忽略 —— 不报错、没人发现，只会越积越多。
#
# ★ 用 mkdtemp() 而不是一个固定路径：多人 / 多个 CI 任务同时跑不会互相踩。
#   这一层是**兜底**，保证"至少没碰真实目录"；每个测试各自的隔离由
#   backend/conftest.py 里的 isolated_media_root fixture 提供（更细，按用例给目录）。
MEDIA_ROOT = tempfile.mkdtemp(prefix="ai-content-agent-test-media-")

# ---- 配图：一场测试都不许真的打中转站 ----
# 配图的测试全部把 get_image_provider 换成假 provider（见 apps/imaging/tests/conftest.py），
# 这里的值是给"没打桩时"兜底用的，也让测试结果不受开发者本地 .env 影响。
# ★ 显式写出来的意义就在这：不写的话，你在 .env 里把额度改成 30，
#   某些测试的成败就会跟着变 —— 那种失败最难查。
IMAGE_API_KEY = "test-image-key-not-real"
IMAGE_BASE_URL = "https://image-relay.invalid/v1"
IMAGE_MODEL = "test-image-model"
IMAGE_TIMEOUT = 5
IMAGE_SIZE = "1024x1536"
IMAGE_QUALITY = "medium"
IMAGE_OUTPUT_FORMAT = "jpeg"
IMAGE_STYLE_PROMPT = "测试用画风"
IMAGE_MAX_PER_ARTICLE = 8
# 0 = 不限制。测额度的那几个用例用 override_settings 临时改成 1 或 2。
IMAGE_DAILY_QUOTA_PER_USER = 0
IMAGE_DAILY_QUOTA_GLOBAL = 0

# ---- 微信公众号：留空，让 get_wechat_client() 抛"未配置" ----
# ★ 这是**故意**留空的：有一条测试专门验证"没配置时返回 400 + 中文提示"。
#   需要真客户端的用例会自己把 get_wechat_client 换成假的（见
#   apps/wechat/tests/conftest.py）。
WECHAT_APP_ID = ""
WECHAT_APP_SECRET = ""
WECHAT_TIMEOUT = 5
WECHAT_AUTHOR = ""

# ---- 让密码哈希变快，测试跑得更迅速 ----
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ---- 日志降噪：测试时不要刷屏 ----
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["apps.agent"]["level"] = "WARNING"  # noqa: F405
