import json
from types import SimpleNamespace

import pytest

from apps.agent.models import AgentRun


BLOCKS = [{'id': 0, 'kind': 'paragraph', 'text': '先验证真实需求，再考虑扩张。'}]


@pytest.mark.django_db
def test_layout_authentication_required(api_client, mocker):
    model = mocker.patch('apps.agent.services.layout_service.get_chat_model')
    assert api_client.post('/api/v1/agent/layout', {'blocks': BLOCKS}, format='json').status_code == 401
    model.assert_not_called()


@pytest.mark.django_db
def test_layout_plan_uses_configured_model_without_creating_article(auth_client, mocker, user):
    plan = {'emphasis': [{'id': 0, 'text': '真实需求', 'style': 'highlight'}]}
    model = mocker.patch('apps.agent.services.layout_service.get_chat_model')
    model.return_value.invoke.return_value = SimpleNamespace(content='```json\n' + json.dumps(plan) + '\n```')
    response = auth_client.post('/api/v1/agent/layout', {'blocks': BLOCKS}, format='json')
    assert response.status_code == 200
    assert response.json()['data']['emphasis'] == plan['emphasis']
    assert response.json()['data']['sections'] == []
    assert not AgentRun.objects.exists()
    model.assert_called_once_with(temperature=0.15, max_tokens=5000, user=user)


@pytest.mark.django_db
@pytest.mark.parametrize('blocks', [[], BLOCKS * 2,
    [{'id': 1, 'kind': 'paragraph', 'text': 'a' * 5001}],
    [{'id': n, 'kind': 'paragraph', 'text': 'a' * 5000} for n in range(5)],
    [{'id': 0, 'kind': 'script', 'text': 'x'}]])
def test_invalid_input_never_calls_provider(auth_client, mocker, blocks):
    model = mocker.patch('apps.agent.services.layout_service.get_chat_model')
    response = auth_client.post('/api/v1/agent/layout', {'blocks': blocks}, format='json')
    assert response.status_code == 400
    model.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize('content', ['not JSON', '{"rewritten":"wrong"}',
    '{"emphasis":[{"id":0,"text":"真实需求","style":"strike"}]}'])
def test_bad_plan_fails_safely(auth_client, mocker, content):
    model = mocker.patch('apps.agent.services.layout_service.get_chat_model')
    model.return_value.invoke.return_value = SimpleNamespace(content=content)
    response = auth_client.post('/api/v1/agent/layout', {'blocks': BLOCKS}, format='json')
    assert response.status_code == 502
    assert '已保留基础排版' in response.json()['error']['message']
    assert content not in response.content.decode()


@pytest.mark.django_db
def test_malformed_plan_retried_once(auth_client, mocker):
    model = mocker.patch('apps.agent.services.layout_service.get_chat_model')
    model.return_value.invoke.side_effect = [SimpleNamespace(content='invalid'), SimpleNamespace(content='{"sections":[]}')]
    response = auth_client.post('/api/v1/agent/layout', {'blocks': BLOCKS}, format='json')
    assert response.status_code == 200
    assert model.return_value.invoke.call_count == 2
