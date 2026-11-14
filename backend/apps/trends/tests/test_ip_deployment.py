import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("configure_ip", ROOT / "deploy/configure_ip.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_ip_switch_preserves_credentials_and_registration_with_exact_backup(tmp_path):
    env = tmp_path / ".env"
    original = (b'# deployment\r\nDJANGO_SECRET_KEY=keep-this-secret\r\n'
                b'DB_PASSWORD="keep$exact"\r\nLLM_API_KEY=\r\nALLOW_REGISTRATION=False\r\n'
                b'SITE_ORIGIN=https://old.example\r\nexport SITE_ORIGIN=https://duplicate.example\r\n'
                b'USE_HTTPS=True\r\n')
    env.write_bytes(original)
    backup = module.configure(env, "87.83.207.66")
    assert backup.read_bytes() == original
    result = env.read_text()
    assert 'DB_PASSWORD="keep$exact"\n' in result
    assert 'DJANGO_SECRET_KEY=keep-this-secret\n' in result
    assert 'LLM_API_KEY=\nALLOW_REGISTRATION=False\n' in result
    assert result.count('SITE_ORIGIN=') == 1
    assert 'SITE_ORIGIN=https://87.83.207.66\n' in result
    assert 'MEDIA_PUBLIC_BASE_URL=https://87.83.207.66\n' in result
    assert 'ALLOWED_HOSTS=87.83.207.66,127.0.0.1,localhost\n' in result
    assert 'WEB_BIND=127.0.0.1\n' in result
    assert 'USE_HTTPS=True\n' in result
    assert module.configure(env, "87.83.207.66") is None
    assert len(list(tmp_path.glob('.env.backup-ip-*'))) == 1


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.1.1', '224.0.0.1', 'example.com', '87.83.207.66/path', '::1'])
def test_invalid_ip_cannot_mutate_environment(tmp_path, address):
    env = tmp_path / '.env'
    env.write_text('DB_PASSWORD=unchanged\n')
    with pytest.raises(ValueError):
        module.configure(env, address)
    assert env.read_text() == 'DB_PASSWORD=unchanged\n'
    assert not list(tmp_path.glob('.env.backup-ip-*'))


def test_missing_environment_does_not_create_blank_configuration(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.configure(tmp_path / '.env', '87.83.207.66')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('invalid_config', [False, True])
def test_ip_script_validates_before_start_and_requires_verified_https(tmp_path, invalid_config):
    bash = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'
    if not Path(bash).exists():
        pytest.skip('POSIX shell unavailable')
    # Exercise the actual orchestration without Docker, networking or real .env mutations.
    harness = r'''
python3() { echo "configure $*" >> "$RUN_LOG"; }
docker() {
  echo "docker $*" >> "$RUN_LOG"
  case "$*" in *'caddy validate'*) [ "$INVALID_CONFIG" != 1 ] ;; esac
}
curl() {
  echo "curl $*" >> "$RUN_LOG"
  case "$*" in *auth/me*) printf 401 ;; esac
}
set -- 87.83.207.66
. "$SCRIPT"
'''
    log = tmp_path / 'run'
    result = subprocess.run([bash, '-c', harness, str(ROOT / 'deploy/use-ip.sh')],
                            env={**os.environ, 'SCRIPT': (ROOT / 'deploy/use-ip.sh').as_posix(), 'RUN_LOG': log.as_posix(),
                                 'INVALID_CONFIG': '1' if invalid_config else '0'},
                            capture_output=True, text=True, timeout=15)
    commands = log.read_text()
    if invalid_config:
        assert result.returncode != 0
        assert 'up -d' not in commands
        assert 'READY:' not in result.stdout
        return
    assert result.returncode == 0, result.stderr
    assert commands.index('caddy validate') < commands.index('up -d --no-build')
    assert commands.index('up -d --no-build') < commands.index('restart web')
    assert '--wait --wait-timeout 180' in commands
    assert 'https://87.83.207.66/health/' in commands
    assert 'https://87.83.207.66/login' in commands
    assert 'https://87.83.207.66/api/v1/auth/me' in commands
    assert 'READY: https://87.83.207.66/login' in result.stdout
    assert '--insecure' not in commands and ' -k ' not in commands
