# ============================================================
# 后端镜像
#
# 构建：docker build -t ai-content-agent-backend ./backend
# 一般不单独构建，用仓库根目录的 docker compose 一起构建。
# ============================================================

# ★ 用 3.11 而不是更新的版本：这个项目的依赖组合
#   （langchain 1.4 + pydantic 2.12 + lxml + trafilatura）
#   是在 3.11.9 上完整跑通 175 个测试验证过的。
#   换版本等于换一套没验证过的依赖树，没有任何好处。
FROM python:3.11-slim

# 不写 .pyc、日志不缓冲（容器里必须，否则 docker logs 看不到实时输出）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ---------- 系统依赖 ----------
# lxml / trafilatura 在 slim 镜像里缺少运行时库。
# 只装运行时（libxml2、libxslt1.1、libffi），不装 -dev 和编译器 ——
# 官方 wheel 已经是预编译好的，不需要现场编译，镜像能小一大截。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        libffi8 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python 依赖 ----------
# 单独一层：只要 requirements.txt 没变，改代码就不会重装依赖。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 应用代码 ----------
# ★ .dockerignore 会把 .env / .venv / db.sqlite3 / staticfiles 排除掉。
#   这一条非常重要：镜像层是可以被解开的（docker save 之后逐层提取），
#   密钥一旦进了镜像层，改 .env 也救不回来，只能重新构建。
COPY . .

# ---------- 启动脚本 ----------
# ★ sed 处理 CRLF 不是多余的：
#   这个项目在 Windows 上开发，entrypoint.sh 很容易被写成 CRLF 换行。
#   带着 \r 的脚本在 Linux 里会报 "/bin/sh^M: bad interpreter: No such file or directory"，
#   这个报错完全指不出真正的原因，能查很久。
#   仓库根的 .gitattributes 已经声明了 *.sh 用 LF，这里再兜一次底。
RUN sed -i 's/\r$//' entrypoint.sh \
    && chmod +x entrypoint.sh \
    && mkdir -p /app/staticfiles

EXPOSE 8000

# 容器活着的判断标准：Django 真的能响应，而不是"进程还在"。
# ★ 走 127.0.0.1，所以 .env 里的 ALLOWED_HOSTS 必须包含 127.0.0.1，
#   否则这个请求会拿到 400，容器永远是 unhealthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
