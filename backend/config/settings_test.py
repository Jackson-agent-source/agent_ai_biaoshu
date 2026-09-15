"""
测试专用配置。

作用：跑 pytest 时不受本地 .env 影响 —— 即使你把 .env 改成 MySQL，
测试也永远用 SQLite 内存数据库，速度快、不污染真实数据。

同时把 LLM / 搜索的调用全部换成假实现，测试绝不消耗你的 API 额度。
"""

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

# ---- 让密码哈希变快，测试跑得更迅速 ----
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ---- 日志降噪：测试时不要刷屏 ----
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["apps.agent"]["level"] = "WARNING"  # noqa: F405
