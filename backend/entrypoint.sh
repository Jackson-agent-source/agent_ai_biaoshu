#!/bin/sh
# ============================================================
# 后端容器启动脚本
#
# 顺序：配置自检 -> 等数据库 -> migrate -> createcachetable
#       -> collectstatic -> gunicorn
#
# 每一步都是**幂等**的，容器重启多少次都不会出问题。
# ============================================================

set -e

echo "==> [1/6] 检查配置"

# ---------- 配置自检 ----------
# ★ 宁可在这里退出，也不要带着错配置启动。
#   带着 SECRET_KEY 的默认值跑起来，任何人都能伪造登录态，
#   而且这种问题从外面完全看不出来。
if [ "$DEBUG" = "True" ] || [ "$DEBUG" = "true" ]; then
    # 不是致命错误，但必须让人看见 —— DEBUG=True 时 Django 会把
    # 完整的 settings 和源码路径渲染到错误页上，公网可见就等于泄漏。
    echo "    !! 警告：DEBUG 是开着的。放到公网前必须改成 False，"
    echo "    !!       否则任何人触发一个报错就能看到你的配置和源码。"
fi

case "$DJANGO_SECRET_KEY" in
    ""|"change-me-to-a-random-string"|"dev-only-insecure-secret-key-change-me"|"dev-only-local-secret-key-please-change-in-production")
        echo "    !! DJANGO_SECRET_KEY 没有换掉，拒绝启动。"
        echo "    !! 生成一个：openssl rand -base64 48"
        echo "    !! 然后写进 .env 里的 DJANGO_SECRET_KEY"
        exit 1
        ;;
esac

if [ -z "$LLM_API_KEY" ]; then
    echo "    !! LLM_API_KEY 是空的，Agent 没法工作。"
    exit 1
fi

# ---------- 等数据库 ----------
echo "==> [2/6] 等待数据库就绪"

# ★ 这里用真实协议握手，不是探端口。
#
#   MySQL 首次初始化时会先起一个"临时 server"（只监听 socket、不接受 TCP），
#   这时端口是通的，但连上去马上就断。用 nc -z 探端口会得到假阳性，
#   然后 migrate 立刻失败、容器进入重启循环。
#
#   用 PyMySQL 真连一次还顺带验证了**认证能不能过** ——
#   比如 MySQL 8 的 caching_sha2_password 需要 cryptography 包，
#   缺了的话会在这里就报出来，而不是等你点"生成文章"才发现。
#
#   （python:3.11-slim 里也没有 nc，只能用 Python。）
python <<'PYEOF'
import os
import sys
import time

import pymysql

settings = {
    "host": os.environ.get("DB_HOST", "db"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "ai_content_agent"),
    "connect_timeout": 5,
}

deadline = time.time() + 180
last_error = None

while time.time() < deadline:
    try:
        conn = pymysql.connect(**settings)
        conn.close()
        print(f"    数据库已就绪 {settings['host']}:{settings['port']}")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 —— 什么错都要重试，直到超时
        last_error = exc
        time.sleep(2)

print(f"    !! 等了 180 秒还是连不上数据库：{last_error}", file=sys.stderr)
sys.exit(1)
PYEOF

# ---------- 迁移 ----------
echo "==> [3/6] 执行数据库迁移"
python manage.py migrate --noinput

# ---------- 缓存表 ----------
# createcachetable 是**幂等**的（官方文档明确说明：只建缺失的表，不碰已有的）。
# 即使 CACHES_BACKEND=locmem（默认）用不到它，建一张空表也没有任何副作用；
# 而一旦你把它切成 db 却忘了建表，所有挂了限流的接口都会 500。
echo "==> [4/6] 准备缓存表"
python manage.py createcachetable

# ---------- 静态文件 ----------
# ★ 不加 --clear：它会先把目标目录清空，whitenoise 正好在这中间读文件的话
#   会返回 404/500。collectstatic 本身就是覆盖式写入，不需要先清。
echo "==> [5/6] 收集静态文件"
python manage.py collectstatic --noinput

# ---------- 启动 ----------
echo "==> [6/6] 启动 gunicorn"

# ★ worker class 用 gthread，不用默认的 sync。
#
#   这个应用的请求 99% 的时间在等 DeepSeek / Tavily 的 HTTP 响应 ——
#   等待时 GIL 是放开的，所以用线程比用进程划算得多。
#   想达到 12 并发，sync worker 要 12 个进程 × 约 350MB ≈ 4GB 内存，
#   gthread 只要 3 个进程 ≈ 1.2GB。
#
# ★ --timeout 120 的说明（容易理解错）：
#   在 gthread 下它不是"请求超过 120 秒就杀掉 worker"。
#   worker 主线程一直在跑事件循环、持续发心跳，所以线程里的长请求不会被它杀掉；
#   它只在主线程真的卡死（比如某个 C 扩展不放 GIL）时才回收 worker。
#   长请求的真正保护是应用层的 LLM_TIMEOUT / SEARCH_TIMEOUT /
#   WEBPAGE_FETCH_TIMEOUT 和 AGENT_RECURSION_LIMIT。
#
# ★ --max-requests：lxml / trafilatura 解析大量网页后内存会缓慢上涨，
#   定期回收 worker 是最省事的解法。jitter 让几个 worker 不要同时重启
#   （同时重启的那一瞬间没有 worker 能接请求）。
#
# ★ 不加 --preload：fork 之前加载应用，容易出共享状态的诡异问题，
#   换来的一点启动速度完全不值得。
#
# ★ 3 workers × 4 threads = 12 并发上限。
#   这同时是"最多 12 个 Agent 同时在跑"的天然闸门 —— 这是好事，不是限制。
#   2 核 4G 的机器上这个配置很舒服（约 1.2GB 后端 + 400MB MySQL）。
#   如果机器只有 2G 内存，把 --workers 改成 2。
#
# ★ 将来如果要把 backend 扩成 2 个副本：
#   必须把上面 migrate 那一步拆成一个独立的 one-shot 服务
#   （用 depends_on: service_completed_successfully 串起来），
#   否则两个副本会同时建表、互相报 "Duplicate column name"。
#   这是扩容时最容易踩的坑。
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --worker-class gthread \
    --workers 3 \
    --threads 4 \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile -
