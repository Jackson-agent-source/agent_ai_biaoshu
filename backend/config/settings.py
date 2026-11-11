"""
Django 全局配置。

设计原则（给初学者的话）：
1. 所有"会变的东西"（密钥、数据库密码、模型名）都从 .env 读取，绝不写死在代码里。
2. 配置集中在这一个文件，其他文件通过 django.conf.settings 读取，不要到处 os.getenv。
3. 本文件不包含任何真实密钥。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# BASE_DIR = backend/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 读取 backend/.env（该文件已在 .gitignore 中，不会被提交）
load_dotenv(BASE_DIR / ".env")


def env_str(key: str, default: str = "") -> str:
    """读取字符串类型环境变量，自动去掉首尾空格和可能误加的引号。"""
    value = os.getenv(key, default)
    if value is None:
        return default
    return value.strip().strip('"').strip("'")


def env_bool(key: str, default: bool = False) -> bool:
    """读取布尔型环境变量，接受 true/1/yes/on。"""
    raw = env_str(key, str(default)).lower()
    return raw in {"true", "1", "yes", "on"}


def env_int(key: str, default: int) -> int:
    """读取整数型环境变量，非法值回退到默认值（避免启动直接崩掉）。"""
    raw = env_str(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    """读取浮点型环境变量，非法值回退到默认值。"""
    raw = env_str(key, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 基础安全配置
# ---------------------------------------------------------------------------
DEBUG = env_bool("DEBUG", True)

# 生产环境必须替换；开发环境给一个明显的占位值
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-change-me")

ALLOWED_HOSTS = [h.strip() for h in env_str("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# 前端（Vite dev server）的地址，允许跨域访问后端 API
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in env_str("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
# 允许携带 Cookie（DRF 的 Session 认证需要）
CORS_ALLOW_CREDENTIALS = True

# ★ 这一项和 CORS_ALLOWED_ORIGINS 是**两件不同的事**，故意拆开：
#     CORS_ALLOWED_ORIGINS  —— 谁可以在浏览器里跨域调我的接口
#     CSRF_TRUSTED_ORIGINS  —— 谁可以带着 Cookie 对我发 POST
#   虽然本项目里两者的值常常相同，但直接复用会在同源部署时出事：
#   前端和后端由同一个 nginx 提供之后，CORS_ALLOWED_ORIGINS 就该留空了，
#   继续耦合会让 CSRF_TRUSTED_ORIGINS 一起变空 ——
#   Django 4.0+ 会强制校验 Origin，结果是 /admin/ 登录直接 403。
#
#   ★ 填的时候必须带协议前缀，写 http://1.2.3.4 而不是 1.2.3.4。
#     只写 IP 是**不生效**的，而且 Django 不会报错、只会默默拒绝，
#     是很难查的一类问题。
CSRF_TRUSTED_ORIGINS = (
    [o.strip() for o in env_str("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
    or CORS_ALLOWED_ORIGINS  # 没配就回退到旧行为，本地开发不受影响
)


# ---------------------------------------------------------------------------
# 生产环境安全开关
# ---------------------------------------------------------------------------
# ★ 域名 + HTTPS 就位之后，把 .env 里的 USE_HTTPS 改成 True，
#   下面这一整块会自动全部生效，代码一个字都不用改。
#   部署步骤（宝塔建站点、反代、加请求头、一键 SSL）见 README 12.10。
#
# ★ 打开它的前提是**外面真的有一层 HTTPS 终结** —— 这套部署里是
#   宿主机上的宝塔 nginx（容器只讲明文 HTTP，只对回环地址讲）。
#   在纯 HTTP 环境打开 = 无限重定向。
#
# ★★ 而且那一层必须发 X-Forwarded-Proto 请求头。Django 判断
#    "这个请求算不算 https"的**唯一**依据就是它（见下面的
#    SECURE_PROXY_SSL_HEADER）—— 少了它，Django 把 https 请求当成 http，
#    301 到 https，绕回来还是被当成 http …… 无限重定向，整站打不开。
#    这个头由宝塔发、由我们容器里的 nginx **原样透传**给后端
#    （frontend/nginx.conf 里用的是 $http_x_forwarded_proto，不是 $scheme）。
USE_HTTPS = env_bool("USE_HTTPS", False)

# 是否启用 whitenoise 来提供静态文件（Admin 的 CSS 就靠它）。
#
# ★ 为什么需要这个开关，而不是直接判断 DEBUG：
#   whitenoise 在 STATIC_ROOT 目录还不存在时，会**每次请求**都打一条
#       UserWarning: No directory at: .../staticfiles/
#   本地开发没跑过 collectstatic，于是测试输出会被这几百条警告刷满，
#   真正有用的警告全被埋了。
#
#   默认跟着 DEBUG 走：生产（DEBUG=False）自动启用，本地自动关闭。
#   config/settings_test.py 会显式把它设成 False —— 因为那个文件是在
#   `from .settings import *` **之后**才改 DEBUG 的，只靠 DEBUG 判断的话，
#   行为会取决于导入顺序，太隐晦了。
STATICFILES_SERVE = env_bool("STATICFILES_SERVE", not DEBUG)

if not DEBUG:
    # ★ SECURE_SSL_REDIRECT 不能在纯 HTTP 环境打开：开了就是无限重定向。
    #   更麻烦的是这个 301 会被浏览器**长期缓存** —— 你改完配置，自己的浏览器
    #   还是跳，得清缓存或者换浏览器才能验证，排查时会怀疑人生。
    SECURE_SSL_REDIRECT = USE_HTTPS
    SESSION_COOKIE_SECURE = USE_HTTPS
    CSRF_COOKIE_SECURE = USE_HTTPS

    if USE_HTTPS:
        # ★ 只在 HTTPS 时设：配了之后 Django 会无条件信任 X-Forwarded-Proto。
        #   正常情况（只有 nginx 能访问后端）没问题，但万一哪天有人把 8000 端口
        #   直接映射到公网，攻击者自己发一个 X-Forwarded-Proto: https 就能骗过
        #   Django。"不设"比"设了指望它没事"省心。
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

        # ★ HSTS 的三个值都做成可配的，因为"打开"和"开多大"是两件事。
        #
        #   下面这两个默认值 = 历史行为，一个字没改；部署模板
        #   （.env.deploy.example）给的是更保守的值：先 300 秒。
        #
        #   为什么要先短后长：HSTS 本身不是不可逆的（浏览器再看到
        #   max-age=0 就解除），真正的风险是**一年 max-age 生效期间
        #   HTTPS 一旦挂掉，浏览器会拒绝回退到明文** ——
        #   等于把一次配置事故放大成一年。稳定一周后再拉长到 31536000。
        SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
        # ★ 含子域会把**这个域名的所有子域**一起锁进 HTTPS。
        #   这个域名以后还要给别的服务用 HTTP 的话，设成 False。
        SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
        # ★ preload 一旦进了浏览器内置列表，想移除要等很久（各个浏览器
        #   发版周期以月计），而且我们并没有提交过。刻意留成可配但默认关。
        SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

        # ★ 给容器自己的 healthcheck 留一条**不跳转**的路。
        #
        #   backend 镜像的 HEALTHCHECK 打的是 http://127.0.0.1:8000/health/，
        #   打开 SECURE_SSL_REDIRECT 之后它会拿到 301 —— 而 `curl -f`
        #   只把 4xx/5xx 当失败，**3xx 照样返回 0**。结果是容器一直显示
        #   healthy，但这个探针其实已经不证明业务层能跑了（假警报）。
        #
        #   regex 匹配的是**去掉开头斜杠**的路径，所以写 ^health/$ 而不是
        #   ^/health/$。这里是公开的健康检查端点，不含任何敏感信息。
        SECURE_REDIRECT_EXEMPT = [r"^health/$"]

    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "same-origin"
    # DRF 的 browsable API 页面需要 JS 读取 csrftoken，所以这里必须是 False
    CSRF_COOKIE_HTTPONLY = False


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
# 缓存给 DRF 的频率限流用来存计数器，默认用进程内存即可。
#
# ★ 为什么默认不是数据库：
#   限流计数器只有在"跨进程共享"时才是准确的。LocMemCache 是每个进程一份，
#   多 worker 下各算各的。但本项目真正防烧钱的是**数 AgentRun 表行数**的那两层
#   每日额度（见 agent_service._enforce_daily_quota），它们不依赖缓存；
#   频率限流只是锦上添花，用内存版足够。
#   等以后真需要精确的跨进程频率限流，把 .env 里的 CACHES_BACKEND 改成 db
#   （entrypoint.sh 会跑 createcachetable 建表），或者换成 Redis，代码不用动。
if env_str("CACHES_BACKEND", "locmem").lower() == "db":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
            # ★ MAX_ENTRIES 必须抬高。默认值是 300，一旦存满，Django 会按
            #   CULL_FREQUENCY=3 砍掉约 1/3 的条目来腾地方 ——
            #   限流计数器被砍掉 = 计数归零 = **限流被静默绕过**。
            "OPTIONS": {"MAX_ENTRIES": 100000},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ai-content-agent",
        }
    }


# ---------------------------------------------------------------------------
# 应用注册
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 第三方
    "rest_framework",
    "rest_framework.authtoken",  # DRF Token 认证（前端用 Authorization: Token xxx）
    "corsheaders",
    # 本项目
    "apps.common",
    "apps.accounts",
    "apps.conversations",
    "apps.agent",
    "apps.trends",
    "apps.articles",
    "apps.imaging",  # AI 配图（文生图）
    "apps.wechat",  # 微信公众号草稿箱
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # 必须尽量靠前
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # 给每个请求分配一个 request_id，方便前后端排查问题（自定义）
    "apps.common.middleware.RequestIDMiddleware",
]

# whitenoise：生产环境由 Django 自己把 staticfiles/ 发出去（Admin 的 CSS 靠它）。
# 官方要求它紧跟在 SecurityMiddleware 后面，所以用 insert 插到第 2 位。
if STATICFILES_SERVE:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
# DB_ENGINE 支持两个值：
#   mysql  —— 正式方案，需要 MySQL 8.0.11 及以上（Django 5.2 的最低要求）
#   sqlite —— 兜底方案，零配置，适合"我还没装 MySQL，但想先把项目跑起来"
DB_ENGINE = env_str("DB_ENGINE", "mysql").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    # PyMySQL 是纯 Python 的 MySQL 驱动，Windows 上不需要编译。
    # Django 默认只认 mysqlclient，这里把它"伪装"成 MySQLdb 让 Django 接受。
    import pymysql

    pymysql.version_info = (2, 2, 8, "final", 0)  # 绕过 Django 的驱动版本检查
    pymysql.install_as_MySQLdb()

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env_str("DB_NAME", "ai_content_agent"),
            "USER": env_str("DB_USER", "root"),
            "PASSWORD": env_str("DB_PASSWORD", ""),
            "HOST": env_str("DB_HOST", "127.0.0.1"),
            "PORT": env_str("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",  # 支持 emoji 和中文
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
            # 连接复用秒数。gunicorn 用 gthread 多线程时，Django 是**每线程一条连接**，
            # 线程会被复用，所以适度复用有收益（省掉每次请求的 TCP + 认证握手）。
            #
            # ★ 为什么要配 CONN_HEALTH_CHECKS：CONN_MAX_AGE > 0 意味着连接被反复使用，
            #   但空闲期间 MySQL 可能已经单方面把它关掉了（wait_timeout 到了、网络抖动），
            #   复用时就会抛 "MySQL server has gone away"。开了健康检查 Django 会先
            #   ping 一下再复用，这是**必须**配对的，不是可选项。
            # ★ 也不要设太大：600 秒的话连接长期占着不放，数据库连接数只增不减。
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# 国际化
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---- 配图（用户上传/生成的媒体文件）----
# ★ 加了这两行**不需要**再动下面的 STORAGES：
#   STORAGES["default"] 本来就是 FileSystemStorage，而它读的正是 MEDIA_ROOT /
#   MEDIA_URL，加这两行就自动生效。多改反而会和 whitenoise 的 staticfiles 打架。
#
# ★ MEDIA_URL 用**绝对路径** /media/（而 STATIC_URL 是相对的 "static/"）：
#     生产：nginx 用 location ^~ /media/ 从共享卷直接发，请求不进 Django
#     开发：DEBUG=True 时 config/urls.py 用 static() 兜住，
#           再由 frontend/vite.config.ts 的 proxy 把 /media 转给 :8000
#   两端拿到的是**同一个字符串**，所以序列化器里的 file_url 不需要任何环境判断。
#
# ★ BASE_DIR / "media" 正好命中根 .gitignore 里已有的 /backend/media/，不用改它。
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---- 复制到公众号：配图的对外基地址 ----
# 只被 apps/imaging/services.public_file_url() 用。粘进 mp.weixin.qq.com 的 HTML
# 必须带**绝对**地址 —— 相对路径在那里会被解析成 mp.weixin.qq.com/media/...。
#
# ★★ **必须和网站本身同源**，例如 https://你的域名（不带端口号）。
#
#    两种写错的症状都是"复制出来的正文文本完全正常、图片全是裂的"，
#    而且很难和别的原因区分：
#      · 线上是 https、这里写了 http://... -> 粘到公众号后被浏览器按
#        **混合内容**拦掉（这是最常见的一种）
#      · 漏掉端口号而网站又确实跑在 8080 上 -> 地址打到了别的端口
#
# ★ 刻意**不**回退到 SITE_ORIGIN：它的默认值（本文件末尾）是 http://localhost:5173，
#   拿它当兜底等于把上面那个坑从后门放进来，而且因为值非空、连 warning 都不会发。
#
# ★ 留空 = 复制时跳过所有配图并提示用户去配（宁可少图，也不要一墙裂图）。
#   上了 HTTPS 之后填 https://你的域名 —— 这同时也是解决"https 页面里的
#   http 图片会被浏览器按混合内容拦掉"的正路。
MEDIA_PUBLIC_BASE_URL = env_str("MEDIA_PUBLIC_BASE_URL", "").rstrip("/")

# Django 4.2+ 用 STORAGES 取代了旧的 STATICFILES_STORAGE
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # ★ 用 CompressedStaticFilesStorage 而不是 CompressedManifestStaticFilesStorage。
    #
    # Manifest 版会给每个静态文件生成一个"原始名 -> 带哈希名"的映射表，缓存效果更好，
    # 但它有个很坑的失败模式：DEBUG=False 时，任何**没有走 {% static %} 标签**的
    # 硬编码引用都会抛
    #     ValueError: Missing staticfiles manifest entry for 'admin/css/base.css'
    # 结果是**整个 /admin/ 直接 500**，而且只往日志里写一行，页面上什么都不显示。
    # Django Admin 和 DRF 的 browsable API 里都有这类引用。
    #
    # 要彻底消除这个风险，得再写一个 manifest_strict = False 的自定义 storage 类。
    # 对"给一小群人用"这个规模，压缩版更简单、更不容易翻车，
    # 代价只是静态文件少一点缓存指纹 —— 完全不值得为它引入一个可能 500 的坑。
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # 前端用 Token（存在 localStorage），Django Admin / 浏览器调试用 Session
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # V1 是单人使用的工具，但接口仍要求登录，避免密钥被随便调用
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPagination",
    "PAGE_SIZE": 10,

    # ---------- 限流 ----------
    # ★ ScopedRateThrottle 只在 view 上写了 throttle_scope 时才生效；
    #   没有 throttle_scope 的 view 会被**直接放行**。
    #   所以把它挂成全局默认是安全的，不会误伤"查历史列表""看文章详情"这类接口。
    #
    # ★ 这只是三层防护里最外面的一层，管的是**频率**不是**总量**。
    #   真正防烧钱的是 apps/agent/services/agent_service.py 里的每日额度
    #   （直接数 AgentRun 表的行数，清缓存/重启/换 worker 都绕不过去）。
    #   原因：把额度刷光最典型的姿势是"每天正常用，但每次都跑满工具调用"，
    #   频率完全正常，只有总量账本挡得住。
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "connection_test": "10/hour",
        # 单用户提交生成的频率。别设太紧：Agent 一次要跑 30~120 秒，
        # 用户在等待期间刷新页面、点开历史列表都不该被误伤。
        "agent_generate": env_str("THROTTLE_GENERATE", "10/hour"),
        "trend_refresh": env_str("THROTTLE_TREND_REFRESH", "3/hour"),
        # 登录接口，防撞库。按 IP + 用户名计数。
        "login": env_str("THROTTLE_LOGIN", "20/min"),
        # 推送公众号草稿箱：每次都要真的调微信（微信有 45009 调用量上限），
        # 而且一次推送要上传 N 张图，比生成一篇文章还费时。
        # ★ 只挂在 POST 上 —— 查推送历史的 GET 不该被限流，
        #   见 apps/wechat/views.py 里 get_throttles 的说明。
        "wechat_publish": env_str("THROTTLE_WECHAT_PUBLISH", "30/hour"),
    },
    # 统一把异常转换成 {success:false, error:{...}} 信封，见 apps/common/exceptions.py
    "EXCEPTION_HANDLER": "apps.common.exceptions.envelope_exception_handler",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}


# ---------------------------------------------------------------------------
# 大模型 Provider（全部来自环境变量，代码里不出现任何 Key）
# ---------------------------------------------------------------------------
LLM_PROVIDER = env_str("LLM_PROVIDER", "openai")
LLM_MODEL = env_str("LLM_MODEL", "")
LLM_API_KEY = env_str("LLM_API_KEY", "")
LLM_BASE_URL = env_str("LLM_BASE_URL", "")
LLM_TEMPERATURE = env_float("LLM_TEMPERATURE", 0.4)
# 单次模型调用超时（秒），防止请求悬挂
LLM_TIMEOUT = env_int("LLM_TIMEOUT", 120)
LLM_MAX_RETRIES = env_int("LLM_MAX_RETRIES", 2)


# ---------------------------------------------------------------------------
# 搜索 Provider
# ---------------------------------------------------------------------------
# 可选值：google / tavily / 留空（留空就不联网搜索）
SEARCH_PROVIDER = env_str("SEARCH_PROVIDER", "").lower()
SEARCH_API_KEY = env_str("SEARCH_API_KEY", "")
SEARCH_BASE_URL = env_str("SEARCH_BASE_URL", "")
SEARCH_TIMEOUT = env_int("SEARCH_TIMEOUT", 20)
# 只有 google 用得上：Programmable Search Engine 的搜索引擎 ID（cx）
SEARCH_ENGINE_ID = env_str("SEARCH_ENGINE_ID", "")
# 结果语言限制，对应 Google 的 lr 参数（例如 lang_en）。
# 默认留空 = 不限制，交给模型自己决定用英文还是中文提问。
# 填 lang_en 就是硬性只要英文网页 —— 注意那样中文提问也基本只会返回英文结果。
SEARCH_LANGUAGE = env_str("SEARCH_LANGUAGE", "")


# ---------------------------------------------------------------------------
# Agent 运行限制（"防失控"）
# 这些数字决定了 Agent 最多能折腾多久，避免它无限搜索下去
# ---------------------------------------------------------------------------
AGENT_LIMITS = {
    # 一次 AgentRun 内最多允许的工具调用次数
    "MAX_TOOL_CALLS": env_int("MAX_TOOL_CALLS", 8),
    # web_search 单次最多返回多少条结果（硬上限 10）
    "MAX_SEARCH_RESULTS": min(env_int("MAX_SEARCH_RESULTS", 5), 10),
    # read_webpage 提取正文后最多保留多少字符
    "MAX_WEBPAGE_CHARS": env_int("MAX_WEBPAGE_CHARS", 20000),
    # 用户输入总长度上限（topic + 其他字段合计）
    "MAX_USER_PROMPT_CHARS": env_int("MAX_USER_PROMPT_CHARS", 5000),
    # LangGraph 的 recursion_limit，最后的硬兜底
    "RECURSION_LIMIT": env_int("AGENT_RECURSION_LIMIT", 30),
    # read_webpage 的网络行为
    "WEBPAGE_FETCH_TIMEOUT": env_int("WEBPAGE_FETCH_TIMEOUT", 15),
    "WEBPAGE_MAX_BYTES": env_int("WEBPAGE_MAX_BYTES", 2_000_000),  # 2MB
    "WEBPAGE_MAX_REDIRECTS": env_int("WEBPAGE_MAX_REDIRECTS", 3),
    # ToolCall.result_preview 最多存多少字符（不存第三方网页全文）
    "RESULT_PREVIEW_CHARS": env_int("RESULT_PREVIEW_CHARS", 500),
    # Source.snippet 最多存多少字符
    "SOURCE_SNIPPET_CHARS": env_int("SOURCE_SNIPPET_CHARS", 500),
}


# ---------------------------------------------------------------------------
# 每日生成额度（防烧钱）
# ---------------------------------------------------------------------------
# ★ 这一组和上面的 AGENT_LIMITS 不是一回事：
#     AGENT_LIMITS  限制的是"一次生成内部"能折腾多久（工具调用次数、递归深度）
#     下面这两个限制的是"一天总共能生成几篇"，是**钱包的止损线**
#
# 两者的区别很关键：把额度刷光最典型的姿势不是"疯狂点按钮"（那会被频率限流挡住），
# 而是"每天正常用，但每次都跑满 8 次工具调用 + 十几次大模型调用"。
# 频率完全正常，只有自然日的总量账本挡得住。
#
# 0 = 不限制（本地开发的默认值，测试不受影响）。
AGENT_DAILY_QUOTA_PER_USER = env_int("AGENT_DAILY_QUOTA_PER_USER", 0)
AGENT_DAILY_QUOTA_GLOBAL = env_int("AGENT_DAILY_QUOTA_GLOBAL", 0)

# 热点采集：Google RSS 无需密钥，其余平台按需开通。
# 多用户部署用 TREND_USERNAMES 指定每个接收选题和草稿的账号。
TREND_GOOGLE_GEO = env_str("TREND_GOOGLE_GEO", "US").upper()
TREND_YOUTUBE_API_KEY = env_str("TREND_YOUTUBE_API_KEY", "")
TREND_GITHUB_TOKEN = env_str("TREND_GITHUB_TOKEN", "")
TREND_GITEE_TOKEN = env_str("TREND_GITEE_TOKEN", "")
TREND_YOUTUBE_REGION = env_str("TREND_YOUTUBE_REGION", "US").upper()
TREND_X_BEARER_TOKEN = env_str("TREND_X_BEARER_TOKEN", "")
TREND_X_WOEID = env_int("TREND_X_WOEID", 1)
TREND_DOUYIN_CLIENT_KEY = env_str("TREND_DOUYIN_CLIENT_KEY", "")
TREND_DOUYIN_CLIENT_SECRET = env_str("TREND_DOUYIN_CLIENT_SECRET", "")
TREND_KEYWORDS = [v.strip().casefold() for v in env_str("TREND_KEYWORDS", "").split(",") if v.strip()]
TREND_AUTO_GENERATE = env_bool("TREND_AUTO_GENERATE", True)
TREND_USER_ID = env_int("TREND_USER_ID", 0)
TREND_USERNAMES = [v.strip() for v in env_str("TREND_USERNAMES", "").split(",") if v.strip()]
TREND_DAILY_ARTICLE_LIMIT = max(0, env_int("TREND_DAILY_ARTICLE_LIMIT", 1))
TREND_FETCH_TIMEOUT = max(3, min(env_int("TREND_FETCH_TIMEOUT", 15), 30))


# ---------------------------------------------------------------------------
# 配图（文生图）
# ---------------------------------------------------------------------------
# ★ 走 OpenAI 兼容协议，所以能直接对接**中转站**（one-api / new-api 那类）：
#   IMAGE_BASE_URL 填中转站的 /v1 地址，IMAGE_API_KEY 填中转站发的 key。
#
# ★ 三样缺一不可，而且**不会**回退到 OpenAI 官方地址 ——
#   中转站的域名每家都不同，给一个默认值只会让人更迷惑。
#   缺任何一项时 get_image_provider() 返回 NullImageProvider，
#   生图接口会返回一句"缺哪几项"的中文提示，而不是一个 500。
IMAGE_API_KEY = env_str("IMAGE_API_KEY", "")
MODEL_CREDENTIAL_SECRET = env_str("MODEL_CREDENTIAL_SECRET", "")
IMAGE_BASE_URL = env_str("IMAGE_BASE_URL", "")
IMAGE_MODEL = env_str("IMAGE_MODEL", "")

# 单张图的超时（秒）。生图本身就要 30~90 秒，往小调必然失败。
IMAGE_TIMEOUT = env_int("IMAGE_TIMEOUT", 300)
# 出图尺寸。横版 1536x1024 —— 和下面 IMAGE_STYLE_PROMPT 里那句"构图为横版"
# 必须保持一致：提示词要横版、尺寸给竖版的话，模型收到的是自相矛盾的指令，
# 出图会不稳定（同一段提示词时而横时而竖）。
# ★ 封面不依赖这个值 —— image_utils.make_wechat_thumb() 自己裁 900x500。
IMAGE_SIZE = env_str("IMAGE_SIZE", "1536x1024")
# quality / output_format 只有 gpt-image-* 系列认，老模型带了会直接 400，
# providers.py 会先判断模型名再决定带不带，这里照填即可。
IMAGE_QUALITY = env_str("IMAGE_QUALITY", "medium")
IMAGE_OUTPUT_FORMAT = env_str("IMAGE_OUTPUT_FORMAT", "jpeg")
# 画风前缀，会拼在每条配图建议前面（拼装逻辑见 imaging/services.py:build_prompt）。
# ★ 默认值里那句"不要出现可读文字"是必须的：
#   生图模型画的中文字几乎全是错的，配图里出现一串乱码字母比没图还难看。
# ★ 这段和 IMAGE_SIZE 是绑死的：它要求横版构图，IMAGE_SIZE 就必须是横向的。
#   改这里的话记得一起看上面那行。
# ★ 想临时换画风不用改这个文件 —— 在 .env 里设 IMAGE_STYLE_PROMPT 即可覆盖。
IMAGE_STYLE_PROMPT = env_str(
    "IMAGE_STYLE_PROMPT",
    """用途：微信公众号深度科技文章横版插图。

统一视觉风格：
高级科技杂志编辑插画，半写实3D，电影级光影。
黑色、午夜蓝和深灰为主要背景色，青绿色为科技主光，
少量红橙色用于表达风险。

构图为横版，视觉中心明确，手机端缩小后仍能看懂。
不要出现可读文字、乱码、品牌Logo、水印和真实软件界面。
不要出现戴兜帽的黑客，不要做成廉价赛博朋克风格。""",
)
# 单篇文章的配图上限。这不是防烧钱（那是下面两个每日额度的事），
# 是防止一篇文章被塞进几十张图 —— 正文图太多公众号里很难读。
# 0 = 不限制。
IMAGE_MAX_PER_ARTICLE = env_int("IMAGE_MAX_PER_ARTICLE", 8)
# 每日配图额度，和 AGENT_DAILY_QUOTA_* 完全同一套思路（数表行数的硬账本，
# 清缓存/重启/换 worker 都绕不过去）。0 = 不限制（本地开发的默认值）。
IMAGE_DAILY_QUOTA_PER_USER = env_int("IMAGE_DAILY_QUOTA_PER_USER", 0)
IMAGE_DAILY_QUOTA_GLOBAL = env_int("IMAGE_DAILY_QUOTA_GLOBAL", 0)


# ---------------------------------------------------------------------------
# 微信公众号
# ---------------------------------------------------------------------------
# ★ 公众号后台 → 设置与开发 → 基本配置 里拿这两个值。
#   ★★ 同一个页面里的 **IP 白名单**必须加上服务器的公网 IP，
#      否则所有接口都会返回 40164。这是后台配置，代码层面无解。
#
# ★ 本项目做的是**建草稿**，不是发布。已认证订阅号只能建草稿，
#   由人在草稿箱里手动点发布 —— 这和产品红线（V1 绝不自动发布）正好一致。
WECHAT_APP_ID = env_str("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = env_str("WECHAT_APP_SECRET", "")
# 微信接口超时（秒）。上传正文图/封面要传几百 KB，别设太小。
WECHAT_TIMEOUT = env_int("WECHAT_TIMEOUT", 60)
# 草稿的"作者"字段。留空就是不带作者，微信允许。
WECHAT_AUTHOR = env_str("WECHAT_AUTHOR", "")


# ---------------------------------------------------------------------------
# 注册开关
# ---------------------------------------------------------------------------
# V1 为了方便本地测试默认开着注册接口。
# ★ 一旦放到公网，建议改成 False：不然任何陌生人注册之后，
#   就能用你的 LLM_API_KEY 生成文章（这钱是你出的）。
#   关掉之后账号由管理员在后台创建。
ALLOW_REGISTRATION = env_bool("ALLOW_REGISTRATION", True)


# ---------------------------------------------------------------------------
# 后台地址
# ---------------------------------------------------------------------------
# 默认还是 admin/（本地开发不用改）。
# ★ 放到公网建议换成一个别人猜不到的路径，例如 manage-8f3a91/：
#   /admin/ 是所有扫描器的第一个目标，换路径能让它们连门都找不到。
#   注意结尾要带斜杠。
ADMIN_URL_PATH = env_str("ADMIN_URL_PATH", "admin/")

# 网站前端的对外地址。只被根路径那个 JSON 提示页用来拼提示语。
#   本地开发：Vite dev server 在 5173（默认值）
#   线上部署：前后端同源，填 https://<你的域名>（不带端口号）
SITE_ORIGIN = env_str("SITE_ORIGIN", "http://localhost:5173").rstrip("/")


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            # 输出示例：[AgentRun=abc123] web_search completed results=5
            "format": "[{asctime}] {levelname} [{name}] {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        # 我们自己写的 agent 相关日志统一走 apps.agent
        "apps.agent": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps.articles": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        # 第三方库日志降噪
        "httpx": {"level": "WARNING", "propagate": True},
        "httpcore": {"level": "WARNING", "propagate": True},
        "urllib3": {"level": "WARNING", "propagate": True},
    },
}
