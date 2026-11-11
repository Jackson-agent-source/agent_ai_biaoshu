import httpx
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound

from apps.accounts.connections import decrypt_key, encrypt_key, public_connection, validate_base_url
from apps.accounts.models import ModelConnection
from apps.accounts.transport import public_client
from apps.common.exceptions import ModelProviderError
from apps.common.responses import success_response


class ConnectionSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    base_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    model_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    api_key = serializers.CharField(max_length=4096, required=False, allow_blank=True, write_only=True)

    def validate_base_url(self, value):
        return validate_base_url(value) if value else ''

    def validate_api_key(self, value):
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise serializers.ValidationError('密钥中不能包含空格、换行或中文。')
        return value


def _kind(value):
    if value not in ('text', 'image'):
        raise NotFound()
    return value


def _merged(user, kind, data):
    serializer = ConnectionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    record = ModelConnection.objects.filter(user=user, kind=kind).first()
    base_url = values.get('base_url', record.base_url if record else '')
    model_name = values.get('model_name', record.model_name if record else '')
    enabled = values.get('enabled', record.enabled if record else False)
    new_key = values.get('api_key', '')
    stored = record.encrypted_key if record else ''
    if stored and base_url != record.base_url and not new_key:
        raise serializers.ValidationError({'api_key': '更换 API 地址时，请重新填写密钥，避免把原密钥发送到新服务。'})
    if enabled and not (base_url and model_name and (new_key or stored)):
        raise serializers.ValidationError('启用个人 API 前，请填写地址、模型名称和密钥。')
    return record, dict(enabled=enabled, base_url=base_url, model_name=model_name), new_key, stored


class ConnectionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response({kind: public_connection(request.user, kind) for kind in ('text', 'image')})


class ConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, kind):
        kind = _kind(kind)
        with transaction.atomic():
            type(request.user).objects.select_for_update().get(pk=request.user.pk)
            _, values, new_key, _ = _merged(request.user, kind, request.data)
            if new_key:
                values['encrypted_key'] = encrypt_key(new_key)
            ModelConnection.objects.update_or_create(user=request.user, kind=kind, defaults=values)
        return success_response(public_connection(request.user, kind))

    def delete(self, request, kind):
        ModelConnection.objects.filter(user=request.user, kind=_kind(kind)).delete()
        return success_response(public_connection(request.user, kind))


class ConnectionTestView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = 'connection_test'

    def post(self, request, kind):
        kind = _kind(kind)
        _, values, new_key, stored = _merged(request.user, kind, request.data)
        if not values['enabled']:
            raise serializers.ValidationError('请先选择个人 API，并填写要测试的配置。测试不会自动保存。')
        base = validate_base_url(values['base_url'], resolve=True)
        key = new_key or decrypt_key(stored)
        try:
            with public_client(timeout=20) as client:
                headers = {'Authorization': 'Bearer ' + key}
                if kind == 'text':
                    response = client.post(base + '/chat/completions', headers=headers, json={
                        'model': values['model_name'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 8,
                    })
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict) or not data.get('choices'):
                        raise ValueError('Unexpected response')
                    message = '文字模型调用成功。测试产生少量文字调用费用。'
                else:
                    response = client.get(base + '/models', headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    models = data.get('data') if isinstance(data, dict) else None
                    if not isinstance(models, list):
                        raise ValueError('Unexpected model list')
                    found = any(isinstance(item, dict) and item.get('id') == values['model_name'] for item in models)
                    message = ('连接成功，模型列表包含所填模型。' if found else '连接成功，但模型列表未包含所填模型，请核对名称。') + '本次未生成图片，尚未验证生图能力。'
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            reasons = {401: '密钥无效或已过期', 403: '没有调用权限', 404: '地址、模型或接口不存在', 429: '额度不足或请求过于频繁'}
            raise ModelProviderError(f'连接测试未通过：{reasons.get(code, "服务返回异常")}（HTTP {code}）。') from None
        except Exception:
            raise ModelProviderError('连接测试未通过，请检查地址、模型名称和网络；服务需兼容 OpenAI 接口。') from None
        return success_response({'message': message, 'image_generation_verified': False} if kind == 'image' else {'message': message})
