"""本地追踪进程的互斥锁和可读取状态，不保存密钥。"""
import json
import os
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def status_path(user_id):
    return settings.BASE_DIR / 'logs' / f'trend-tracking-{int(user_id)}.json'


def write_status(user_id, payload):
    path = status_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({**payload, 'heartbeat_at': timezone.now().isoformat()}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def read_status(user_id):
    try:
        data = json.loads(status_path(user_id).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {'active': False, 'state': 'not_running'}
        heartbeat = parse_datetime(data.get('heartbeat_at', ''))
        data['active'] = bool(heartbeat and heartbeat.tzinfo and timezone.now() - heartbeat < timedelta(minutes=3)
                              and data.get('state') in {'collecting', 'waiting', 'retrying'})
        return data
    except (OSError, ValueError, TypeError):
        return {'active': False, 'state': 'not_running'}


@contextmanager
def tracking_lock(user_id):
    path = status_path(user_id).with_suffix('.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
