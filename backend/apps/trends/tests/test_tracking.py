from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.trends.tracking import read_status, status_path, tracking_lock, write_status


@pytest.fixture(autouse=True)
def isolated_tracking_files(settings, tmp_path):
    settings.BASE_DIR = tmp_path


@pytest.mark.django_db
def test_watcher_collects_and_builds_without_generating_articles(user, mocker):
    collect = mocker.patch('apps.trends.management.commands.watch_trends.collect_focus_trends',
        return_value=SimpleNamespace(status='success', item_count=12, message=''))
    build = mocker.patch('apps.trends.management.commands.watch_trends.build_topics', return_value=[object()])
    generate = mocker.patch('apps.trends.services.AgentService.generate_article')
    call_command('watch_trends', user_id=user.pk, interval=3600, once=True)
    collect.assert_called_once()
    build.assert_called_once()
    generate.assert_not_called()
    status = read_status(user.pk)
    assert status['state'] == 'completed'
    assert status['item_count'] == 12
    assert status['last_success_at']
    assert status['active'] is False


@pytest.mark.django_db
def test_failed_collection_does_not_build_from_stale_items(user, mocker):
    mocker.patch('apps.trends.management.commands.watch_trends.collect_focus_trends',
        return_value=SimpleNamespace(status='failed', item_count=0, message='连接失败'))
    build = mocker.patch('apps.trends.management.commands.watch_trends.build_topics')
    with pytest.raises(CommandError, match='连接失败'):
        call_command('watch_trends', user_id=user.pk, once=True)
    build.assert_not_called()
    assert read_status(user.pk)['last_status'] == 'failed'


def test_tracking_lock_prevents_duplicate_worker_and_releases():
    with tracking_lock(1):
        with pytest.raises(OSError):
            with tracking_lock(1):
                pass
    with tracking_lock(1):
        pass


def test_status_detects_missing_stale_stopped_and_corrupt_heartbeat():
    assert read_status(1)['active'] is False
    write_status(1, {'state': 'waiting', 'interval_seconds': 3600})
    assert read_status(1)['active'] is True
    import json
    status_path(1).write_text(json.dumps({'state': 'waiting', 'heartbeat_at': (timezone.now()-timedelta(minutes=4)).isoformat()}), encoding='utf-8')
    assert read_status(1)['active'] is False
    write_status(1, {'state': 'stopped'})
    assert read_status(1)['active'] is False
    status_path(1).write_text('[]', encoding='utf-8')
    assert read_status(1)['active'] is False


@pytest.mark.django_db
def test_focus_only_pipeline_can_disable_automatic_articles(user, mocker):
    collect = mocker.patch('apps.trends.management.commands.run_trend_pipeline.collect_trends')
    mocker.patch('apps.trends.management.commands.run_trend_pipeline.collect_focus_trends', return_value=SimpleNamespace(status='success',item_count=1,message=''))
    mocker.patch('apps.trends.management.commands.run_trend_pipeline.build_topics', return_value=[])
    generate = mocker.patch('apps.trends.management.commands.run_trend_pipeline.auto_generate')
    call_command('run_trend_pipeline', user_id=user.pk, focus_only=True, no_generate=True)
    collect.assert_not_called()
    generate.assert_not_called()
