"""检查 Compose 接线和真实 POSIX 调度循环，不启动外部服务。"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


BACKEND = Path(__file__).resolve().parents[3]
ROOT = BACKEND.parent


def test_compose_scheduler_uses_backend_database_and_media():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    backend, scheduler = services["backend"], services["trend_scheduler"]
    assert backend["env_file"] == scheduler["env_file"] == [".env"]
    assert backend["environment"] == scheduler["environment"] == {
        "DB_ENGINE": "mysql", "DB_HOST": "db", "DB_PORT": "3306",
    }
    assert scheduler["depends_on"]["backend"]["condition"] == "service_healthy"
    assert scheduler["entrypoint"] == ["/bin/sh", "/app/trend_scheduler.sh"]
    assert "uploads:/app/media" in backend["volumes"]
    assert "uploads:/app/media" in scheduler["volumes"]
    assert scheduler["healthcheck"]["disable"] is True


@pytest.mark.parametrize("interval, expected", [("1", "900"), ("invalid", "21600"), ("3600", "3600")])
def test_scheduler_retries_and_stops_on_term(tmp_path, interval, expected):
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("POSIX shell unavailable")
    # 使用 shell 函数替换两个外部进程，仍实际执行生产脚本的循环、wait 和 trap。
    harness = r'''
python() {
  echo "$*" >> "$RUN_LOG"
  case "$(wc -l < "$RUN_LOG")" in
    1) return 1 ;;
    *) return 0 ;;
  esac
}
sleep() {
  echo "$1" >> "$SLEEP_LOG"
  if [ "$(wc -l < "$SLEEP_LOG")" -ge 2 ]; then kill -TERM "$$"; fi
}
. "$0"
'''
    result = subprocess.run(
        [bash, "-c", harness, (BACKEND / "trend_scheduler.sh").as_posix()],
        cwd=tmp_path, env={**os.environ, "TREND_INTERVAL_SECONDS": interval,
                          "RUN_LOG": (tmp_path / "runs").as_posix(),
                          "SLEEP_LOG": (tmp_path / "sleeps").as_posix()},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runs").read_text().splitlines() == ["manage.py run_trend_pipeline"] * 2
    assert (tmp_path / "sleeps").read_text().splitlines() == [expected] * 2
    assert "Trend pipeline failed" in result.stderr
    assert "Trend pipeline completed" in result.stdout
