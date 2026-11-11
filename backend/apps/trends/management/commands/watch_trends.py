"""电脑开机时持续采集个人研究方向；不自动生成文章。"""
import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from apps.trends.services import build_topics, collect_focus_trends
from apps.trends.tracking import tracking_lock, write_status

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '持续追踪指定账号的研究方向，默认每小时采集并更新选题，不自动生成文章'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--interval', type=int, default=3600, help='每轮完成后的等待秒数，至少900')
        parser.add_argument('--once', action='store_true', help='只执行一轮')

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(pk=options['user_id'], is_active=True).first()
        if not user:
            raise CommandError('请指定有效的本地用户 ID')
        interval = max(900, options['interval'])
        # 先独立获取锁，避免把采集/数据库的 OSError 误报成重复进程。
        lock = tracking_lock(user.pk)
        try:
            lock.__enter__()
        except OSError as exc:
            raise CommandError('该账号已有追踪进程，请勿重复启动') from exc
        state = {'interval_seconds': interval, 'state': 'collecting'}
        try:
            while True:
                close_old_connections()
                user.refresh_from_db()
                if not user.is_active:
                    raise CommandError('账号已禁用，追踪已停止')
                state.update(state='collecting', last_started_at=timezone.now().isoformat(), next_run_at=None)
                write_status(user.pk, state)
                self.stdout.write(f'{timezone.localtime():%Y-%m-%d %H:%M:%S} 开始追踪 {user.username}')
                try:
                    run = collect_focus_trends(user=user)
                    state.update(item_count=run.item_count, message=run.message, last_status=run.status)
                    if run.status == 'success':
                        candidates = build_topics(user=user)
                        state.update(candidate_count=len(candidates), last_success_at=timezone.now().isoformat())
                    else:
                        state['candidate_count'] = 0
                    self.stdout.write(f'采集 {run.status}：{run.item_count} 条；选题 {state["candidate_count"]} 条；{run.message}')
                except Exception:
                    logger.exception('研究方向追踪失败 user=%s', user.pk)
                    state.update(last_status='failed', message='追踪异常，请查看本地日志；下轮将重试')
                if options['once']:
                    state['state'] = 'completed'
                    write_status(user.pk, state)
                    if state['last_status'] != 'success':
                        raise CommandError(state['message'])
                    return
                deadline = time.monotonic() + interval
                state.update(state='waiting' if state['last_status'] == 'success' else 'retrying',
                             next_run_at=(timezone.now() + timedelta(seconds=interval)).isoformat())
                while time.monotonic() < deadline:
                    write_status(user.pk, state)
                    time.sleep(min(60, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            self.stdout.write('追踪已停止')
        finally:
            if state['state'] != 'completed':
                state.update(state='stopped', next_run_at=None)
                write_status(user.pk, state)
            lock.__exit__(None, None, None)
            close_old_connections()
