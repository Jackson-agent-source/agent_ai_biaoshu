from datetime import timedelta

import pytest
from django.utils import timezone

from apps.trends.models import TopicCandidate, TrendFetchRun, TrendItem


@pytest.mark.django_db
@pytest.mark.parametrize('enabled,limit', [(True, 3), (False, 1), (True, 0)])
def test_overview_exposes_actual_automation_configuration(auth_client, settings, enabled, limit):
    settings.TREND_AUTO_GENERATE = enabled
    settings.TREND_DAILY_ARTICLE_LIMIT = limit
    response = auth_client.get('/api/v1/trends/overview')
    assert response.status_code == 200
    assert response.json()['data']['automation'] == {
        'enabled': enabled, 'daily_article_limit': limit,
    }


@pytest.mark.django_db
def test_changed_focus_is_reflected_in_overview_and_cannot_generate_old_topic(auth_client, user, mocker):
    trend = TrendItem.objects.create(
        platform='focus_news', external_id='old-focus', title='旧关注方向',
        category='focus', owner=user, url='https://example.org/old',
    )
    candidate = TopicCandidate.objects.create(user=user, cluster_key='old-focus', topic='旧选题', category='focus')
    candidate.trends.add(trend)
    generate = mocker.patch('apps.trends.services.AgentService.generate_article')
    response = auth_client.put('/api/v1/trends/preferences', {
        'focus_terms': ['机器人'], 'auto_categories': ['focus'],
    }, format='json')
    assert response.status_code == 200
    overview = auth_client.get('/api/v1/trends/overview').json()['data']
    assert overview['preferences']['focus_terms'] == ['机器人']
    assert overview['candidates'][0]['status'] == 'skipped'
    response = auth_client.post(f'/api/v1/trends/topics/{candidate.pk}/generate', {}, format='json')
    assert response.status_code == 400
    generate.assert_not_called()


@pytest.mark.django_db
def test_source_overview_returns_latest_user_scoped_run_with_timestamp(auth_client, user, other_user):
    previous = TrendFetchRun.objects.create(platform='google', status='success', item_count=10)
    TrendFetchRun.objects.filter(pk=previous.pk).update(created_at=timezone.now() - timedelta(hours=1))
    latest = TrendFetchRun.objects.create(platform='google', status='failed', message='暂时不可用')
    own = TrendFetchRun.objects.create(platform='focus_news', user=user, status='success', item_count=2)
    TrendFetchRun.objects.create(platform='focus_news', user=other_user, status='failed', message='其他用户')
    runs = {run['platform']: run for run in auth_client.get('/api/v1/trends/overview').json()['data']['sources']}
    assert runs['google']['status'] == latest.status
    assert runs['google']['message'] == '暂时不可用'
    assert runs['google']['created_at']
    assert runs['focus_news']['status'] == own.status
    assert runs['focus_news']['item_count'] == 2
