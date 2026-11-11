import base64
import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from rest_framework.exceptions import ValidationError

from apps.accounts.connections import decrypt_key, personal_connection, validate_base_url, public_address
from apps.accounts.models import ModelConnection
from apps.accounts.transport import PublicTransport
from apps.agent.services.model_factory import get_chat_model, get_model_name
from apps.imaging.providers import get_image_provider, PersonalImageProvider

URL = '/api/v1/auth/api-settings'
CONFIG = {'enabled': True, 'base_url': 'https://models.example.com/v1', 'model_name': 'personal-model', 'api_key': 'test-private-key'}


@pytest.fixture
def public_dns(mocker):
    return mocker.patch('apps.accounts.connections.socket.getaddrinfo', return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])


@pytest.mark.django_db
def test_anonymous_cannot_read_write_or_test(api_client):
    assert api_client.get(URL).status_code == 401
    assert api_client.put(URL + '/text', CONFIG, format='json').status_code == 401
    assert api_client.post(URL + '/text/test', CONFIG, format='json').status_code == 401


@pytest.mark.django_db
def test_account_isolation_encryption_and_key_replacement(auth_client, other_auth_client, user):
    response = auth_client.put(URL + '/text', CONFIG, format='json')
    assert response.status_code == 200
    assert response.json()['data']['has_key'] is True
    assert CONFIG['api_key'] not in response.content.decode()
    record = ModelConnection.objects.get(user=user, kind='text')
    assert record.encrypted_key != CONFIG['api_key']
    assert decrypt_key(record.encrypted_key) == CONFIG['api_key']
    assert 'encrypted_key' not in auth_client.get(URL).content.decode()
    assert other_auth_client.get(URL).json()['data']['text']['has_key'] is False
    assert auth_client.put(URL + '/text', {'model_name': 'updated', 'api_key': ''}, format='json').status_code == 200
    record.refresh_from_db()
    assert decrypt_key(record.encrypted_key) == CONFIG['api_key']
    assert auth_client.put(URL + '/text', {'base_url': 'https://different.example.com/v1'}, format='json').status_code == 400
    assert auth_client.put(URL + '/text', {'base_url': 'https://different.example.com/v1', 'api_key': 'replacement'}, format='json').status_code == 200
    record.refresh_from_db()
    assert decrypt_key(record.encrypted_key) == 'replacement'
    other_auth_client.delete(URL + '/text')
    assert ModelConnection.objects.filter(user=user).exists()
    assert auth_client.delete(URL + '/text').status_code == 200
    assert not ModelConnection.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_enabled_missing_key_and_unknown_kind_rejected(auth_client):
    assert auth_client.put(URL + '/text', {**CONFIG, 'api_key': ''}, format='json').status_code == 400
    assert auth_client.put(URL + '/unknown', CONFIG, format='json').status_code == 404


@pytest.mark.parametrize('url', ['http://example.com/v1', 'https://127.0.0.1/v1', 'https://10.0.0.1/v1',
    'https://[::1]/v1', 'https://localhost/v1', 'https://x.local/v1', 'https://user:password@example.com/v1',
    'https://example.com/v1?key=secret', 'https://example.com:8000/v1', 'https://example.com/v1/chat/completions'])
def test_disallowed_destinations(url):
    with pytest.raises(ValidationError):
        validate_base_url(url)


def test_transport_pins_ip_keeps_tls_and_rejects_rebinding(mocker, public_dns):
    send = mocker.patch.object(httpx.HTTPTransport, 'handle_request', return_value=httpx.Response(200))
    with PublicTransport() as transport:
        request = httpx.Request('POST', CONFIG['base_url'] + '/chat/completions', content=b'{}')
        transport.handle_request(request)
        pinned = send.call_args.args[0]
        assert pinned.url.host == '8.8.8.8'
        assert pinned.headers['host'] == 'models.example.com'
        assert pinned.extensions['sni_hostname'] == 'models.example.com'
        public_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with pytest.raises(httpx.ConnectError):
            transport.handle_request(request)
    assert send.call_count == 1


def test_fake_dns_is_resolved_independently_and_still_rejects_private_answers(mocker, public_dns):
    public_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.0.42', 443))]
    def make_response(ip):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'Answer': [{'type': 1, 'data': ip}]})))
    safe_client = make_response('8.8.4.4')
    mocker.patch('apps.accounts.connections.httpx.Client', return_value=safe_client)
    assert public_address('models.example.com') == '8.8.4.4'
    from django.core.cache import cache
    cache.clear()
    # Build through the original class to avoid the patched constructor.
    unsafe_client = type(safe_client)(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'Answer': [{'type': 1, 'data': '127.0.0.1'}]})))
    mocker.patch('apps.accounts.connections.httpx.Client', return_value=unsafe_client)
    with pytest.raises(ValidationError):
        public_address('models.example.com')


@pytest.mark.django_db
def test_layout_uses_saved_account_model(auth_client, other_user, user, mocker, public_dns, settings):
    auth_client.put(URL + '/text', CONFIG, format='json')
    factory = mocker.patch('langchain_openai.ChatOpenAI')
    factory.return_value.invoke.return_value = SimpleNamespace(content='{"emphasis":[]}')
    response = auth_client.post('/api/v1/agent/layout', {'blocks': [{'id': 0, 'kind': 'paragraph', 'text': '正文。'}]}, format='json')
    assert response.status_code == 200
    kwargs = factory.call_args.kwargs
    assert kwargs['api_key'] == CONFIG['api_key']
    assert kwargs['base_url'] == CONFIG['base_url']
    assert get_model_name(user=user) == 'personal-model'
    assert personal_connection(other_user, 'text') is None
    settings.LLM_API_KEY = 'server-key'
    settings.LLM_MODEL = 'server-model'
    auth_client.put(URL + '/text', {'enabled': False}, format='json')
    get_chat_model(user=user)
    assert factory.call_args.kwargs['api_key'] == 'server-key'


@pytest.mark.django_db
def test_connection_test_is_real_request_but_does_not_save(auth_client, user, mocker, public_dns):
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}]})
    mocker.patch('apps.accounts.connection_views.public_client', side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(respond)))
    response = auth_client.post(URL + '/text/test', CONFIG, format='json')
    assert response.status_code == 200
    assert not ModelConnection.objects.filter(user=user).exists()
    assert captured[0].headers['authorization'] == 'Bearer test-private-key'
    assert json.loads(captured[0].content)['model'] == 'personal-model'


@pytest.mark.django_db
def test_error_response_never_echoes_secret(auth_client, mocker, public_dns):
    mocker.patch('apps.accounts.connection_views.public_client', side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(401, json={'error': CONFIG['api_key']}))))
    response = auth_client.post(URL + '/text/test', CONFIG, format='json')
    assert response.status_code == 502
    assert CONFIG['api_key'] not in response.content.decode()


@pytest.mark.django_db
def test_image_test_reports_limits_without_generating(auth_client, mocker, public_dns):
    def respond(request):
        assert request.method == 'GET' and request.url.path == '/v1/models'
        return httpx.Response(200, json={'data': [{'id': 'personal-model'}]})
    mocker.patch('apps.accounts.connection_views.public_client', side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(respond)))
    response = auth_client.post(URL + '/image/test', CONFIG, format='json')
    assert response.status_code == 200
    assert response.json()['data']['image_generation_verified'] is False


@pytest.mark.django_db
def test_image_generation_uses_personal_key_and_saves_correct_model(auth_client, user, mocker, public_dns):
    from apps.articles.models import Article
    from apps.imaging.services import generate_image
    from apps.imaging.tests.conftest import make_image_bytes
    auth_client.put(URL + '/image', CONFIG, format='json')
    provider = get_image_provider(user=user)
    assert isinstance(provider, PersonalImageProvider)
    def respond(request):
        assert request.headers['authorization'] == 'Bearer test-private-key'
        assert json.loads(request.content)['model'] == 'personal-model'
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(make_image_bytes()).decode()}]})
    mocker.patch('apps.imaging.providers.public_client', side_effect=lambda *args, **kw: httpx.Client(transport=httpx.MockTransport(respond)))
    article = Article.objects.create(user=user, title='示例', content_markdown='正文')
    record = generate_image(user=user, article=article, suggestion='科技封面')
    assert record.status == 'succeeded'
    assert record.model_name == 'personal-model'
    assert record.image_file


@pytest.mark.django_db
def test_article_generation_reads_personal_connection(auth_client, user, mocker, public_dns):
    from apps.agent.services.agent_service import AgentService
    from apps.agent.tests.fakes import ScriptedChatModel, make_article_draft_message, sample_draft_args
    auth_client.put(URL + '/text', CONFIG, format='json')
    fake = ScriptedChatModel(responses=[make_article_draft_message(sample_draft_args())])
    factory = mocker.patch('langchain_openai.ChatOpenAI', return_value=fake)
    article, run = AgentService.generate_article(user=user, request_data={'topic': '本地测试', 'research_mode': False})
    assert article.model_name == 'personal-model'
    assert run.status == 'completed'
    assert factory.call_args.kwargs['api_key'] == 'test-private-key'
