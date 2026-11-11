import pytest

from apps.trends.models import TopicCandidate, TrendItem, TrendPreference


@pytest.mark.django_db
def test_restore_own_topic_preserves_research_content(auth_client, user, other_auth_client):
    candidate = TopicCandidate.objects.create(user=user, cluster_key='restore', topic='AI代理权限边界',
        angle='治理与审计', status='skipped', error_message='旧方向')
    item = TrendItem.objects.create(platform='bbc_tech', external_id='source', title='AI story', url='https://example.org/ai')
    candidate.trends.add(item)
    url = f'/api/v1/trends/topics/{candidate.pk}/restore'
    assert other_auth_client.post(url, {}, format='json').status_code == 404
    candidate.refresh_from_db()
    assert candidate.status == 'skipped'
    response = auth_client.post(url, {}, format='json')
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'ready'
    candidate.refresh_from_db()
    assert candidate.error_message == ''
    assert candidate.topic == 'AI代理权限边界'
    assert candidate.angle == '治理与审计'
    assert candidate.trends.get().pk == item.pk
    assert auth_client.post(url, {}, format='json').status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize('status', ['ready', 'generating', 'generated', 'failed'])
def test_restore_does_not_reset_other_states(auth_client, user, status):
    candidate = TopicCandidate.objects.create(user=user, cluster_key=status, topic='保护状态', status=status)
    assert auth_client.post(f'/api/v1/trends/topics/{candidate.pk}/restore', {}, format='json').status_code == 400
    candidate.refresh_from_db()
    assert candidate.status == status


@pytest.mark.django_db
def test_changing_focus_keeps_retained_direction_and_public_topic(auth_client, user):
    preference, _ = TrendPreference.objects.get_or_create(user=user)
    preference.focus_terms = ['AI工具研究', 'BTC']
    preference.save()
    for name, owner, label in [('retained', user, 'AI工具研究'), ('removed', user, 'BTC'), ('public', None, '')]:
        item = TrendItem.objects.create(platform='focus_news' if owner else 'bbc_tech', external_id=name,
            title=name, url=f'https://example.org/{name}', owner=owner, research_topic=label, category='focus')
        candidate = TopicCandidate.objects.create(user=user, cluster_key=name, topic=name, category='focus')
        candidate.trends.add(item)
    response = auth_client.put('/api/v1/trends/preferences', {
        'focus_terms': ['AI工具研究', '港卡'], 'auto_categories': ['focus'],
    }, format='json')
    assert response.status_code == 200
    assert TopicCandidate.objects.get(cluster_key='retained').status == 'ready'
    assert TopicCandidate.objects.get(cluster_key='public').status == 'ready'
    removed = TopicCandidate.objects.get(cluster_key='removed')
    assert removed.status == 'skipped'
    assert '可以恢复' in removed.error_message
