import httpx
import pytest

from apps.trends import providers
from apps.trends.categories import clean_focus_terms
from apps.trends.models import TrendItem
from apps.trends.providers import TrendProviderError, TrendRecord
from apps.trends.services import build_topics, collect_focus_trends


def test_focus_terms_accept_research_list_and_limit():
    assert clean_focus_terms('AI工具研究｜AI落地企业|AI赋能行业，投资，美股，BTC，纳指，港卡') == [
        'AI工具研究', 'AI落地企业', 'AI赋能行业', '投资', '美股', 'BTC', '纳指', '港卡',
    ]
    assert len(clean_focus_terms([f'主题{i}' for i in range(16)])) == 16
    with pytest.raises(ValueError, match='16'):
        clean_focus_terms([f'主题{i}' for i in range(17)])


def test_each_research_direction_is_searched_separately_and_deduplicated(mocker):
    queries = []
    real_client = httpx.Client

    def reply(request):
        query = request.url.params['q']
        queries.append(query)
        topic = 'bank' if '香港银行' in query else 'btc'
        xml = f'<rss><channel><item><title>{topic} story</title><link>https://example.org/{topic}</link><guid>{topic}</guid></item><item><title>Shared</title><link>https://example.org/shared</link><guid>shared</guid></item></channel></rss>'
        return httpx.Response(200, text=xml)

    mocker.patch.object(providers.httpx, 'Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw))
    records = providers.fetch_focus_news(['BTC', '港卡'], user_id=1)
    assert len(queries) == 2
    assert any('Bitcoin ETF' in query and 'when:7d' in query for query in queries)
    assert any('香港银行开户' in query for query in queries)
    assert [record.title for record in records] == ['btc story', 'bank story', 'Shared']
    assert [record.research_topic for record in records[:2]] == ['BTC', '港卡']
    assert len({record.external_id for record in records}) == 3


def test_one_direction_failure_does_not_discard_other_direction(mocker):
    def fetch(term, user_id):
        if term == '港卡':
            raise TrendProviderError('关注方向资讯获取失败（ConnectTimeout）')
        return [TrendRecord('focus_news', 'btc', 'Bitcoin', 'https://example.org/btc', research_topic=term)]
    mocker.patch.object(providers, '_focus_news_for_term', side_effect=fetch)
    records = providers.fetch_focus_news(['BTC', '港卡'], 1)
    assert len(records) == 1
    assert '港卡' in records.warnings[0]


def test_all_direction_failures_are_reported(mocker):
    mocker.patch.object(providers, '_focus_news_for_term', side_effect=TrendProviderError('timeout'))
    with pytest.raises(TrendProviderError, match='所有研究方向'):
        providers.fetch_focus_news(['BTC', '港卡'], 1)


def test_focus_html_is_not_reported_as_empty_success(mocker):
    real_client = httpx.Client
    mocker.patch.object(providers.httpx, 'Client', side_effect=lambda **kw: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text='<html>blocked</html>', headers={'content-type': 'text/html'})), **kw))
    with pytest.raises(TrendProviderError, match='返回网页'):
        providers.fetch_focus_news(['BTC'], 1)


@pytest.mark.django_db
def test_research_collection_persists_direction_and_partial_failure_message(user, mocker):
    records = providers.FocusNewsBatch([
        TrendRecord('focus_news', 'one', 'Bitcoin ETF研究', 'https://example.org/one', research_topic='BTC'),
    ], ['港卡：连接超时'])
    mocker.patch('apps.trends.services.fetch_focus_news', return_value=records)
    run = collect_focus_trends(user=user)
    assert run.status == 'success'
    assert '港卡' in run.message
    assert TrendItem.objects.get(owner=user).research_topic == 'BTC'


@pytest.mark.django_db
def test_topic_selection_keeps_seat_for_each_research_direction(user, mocker):
    from apps.trends.models import TrendPreference
    preference, _ = TrendPreference.objects.get_or_create(user=user)
    preference.focus_terms = ['AI工具研究', 'BTC', '港卡']
    preference.save()
    for index in range(15):
        TrendItem.objects.create(platform='focus_news', external_id=f'ai-{index}', title=f'AI产品案例 {index}',
            category='focus', owner=user, research_topic='AI工具研究', url=f'https://example.org/ai/{index}', rank=1)
    for index, label in enumerate(['BTC', '港卡']):
        TrendItem.objects.create(platform='focus_news', external_id=f'other-{index}', title=f'{label}新规则',
            category='focus', owner=user, research_topic=label, url=f'https://example.org/other/{index}', rank=6)
    mocker.patch('apps.trends.services.model_factory.is_model_configured', return_value=False)
    candidates = build_topics(user=user, limit=3)
    labels = {trend.research_topic for candidate in candidates for trend in candidate.trends.all()}
    assert labels == {'AI工具研究', 'BTC', '港卡'}


@pytest.mark.django_db
def test_research_presets_and_extended_preferences_are_user_scoped(auth_client, user, other_user):
    from apps.trends.models import TrendPreference
    response = auth_client.get('/api/v1/trends/overview').json()['data']
    assert response['max_focus_terms'] == 16
    assert {'Web3', 'AI出海', '美股'} <= {preset['label'] for preset in response['research_presets']}
    terms = [preset['label'] for preset in response['research_presets']]
    changed = auth_client.put('/api/v1/trends/preferences', {'focus_terms': terms, 'auto_categories': []}, format='json')
    assert changed.status_code == 200
    assert TrendPreference.objects.get(user=user).focus_terms == terms
    assert not TrendPreference.objects.filter(user=other_user, focus_terms=terms).exists()
