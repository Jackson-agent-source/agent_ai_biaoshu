#!/bin/sh
# 生产环境单实例运行。后台服务健康后由 Compose 启动此脚本。
set -eu

# 同时支持从工作目录外手动启动；Compose 内该目录是 /app。
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

interval="${TREND_INTERVAL_SECONDS:-21600}"
case "$interval" in
  ''|*[!0-9]*) interval=21600 ;;
esac
if [ "$interval" -lt 900 ]; then interval=900; fi

child=""
stop() {
  trap - TERM INT
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  exit 0
}
trap stop TERM INT

while :; do
  echo "==> Running trend pipeline"
  python manage.py run_trend_pipeline &
  child=$!
  if wait "$child"; then
    echo "==> Trend pipeline completed"
  else
    echo "!! Trend pipeline failed; retrying after ${interval}s" >&2
  fi
  sleep "$interval" &
  child=$!
  wait "$child" || true
done
