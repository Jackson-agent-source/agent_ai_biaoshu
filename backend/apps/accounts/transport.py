"""Pin public DNS destinations while preserving Host, TLS SNI and certificate checks."""
import httpx
from rest_framework.exceptions import ValidationError
from apps.accounts.connections import public_address


def _pinned_request(request):
    if request.url.scheme != 'https' or request.url.port not in (None, 443):
        raise httpx.ConnectError('Only public HTTPS endpoints are supported', request=request)
    try:
        address = public_address(request.url.host)
    except ValidationError:
        raise httpx.ConnectError('API destination is not a reachable public host', request=request) from None
    headers = request.headers.copy()
    headers['Host'] = request.url.netloc.decode('ascii')
    return httpx.Request(request.method, request.url.copy_with(host=address), headers=headers,
                         stream=request.stream, extensions={**request.extensions, 'sni_hostname': request.url.host})


class PublicTransport(httpx.HTTPTransport):
    def handle_request(self, request):
        return super().handle_request(_pinned_request(request))


class AsyncPublicTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        return await super().handle_async_request(_pinned_request(request))


def public_client(timeout=30):
    return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, transport=PublicTransport())
