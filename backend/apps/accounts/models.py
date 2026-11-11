from django.conf import settings
from django.db import models


class ModelConnection(models.Model):
    """Per-account OpenAI-compatible connections; secrets are never serialized."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=[('text', '文字模型'), ('image', '图片模型')])
    enabled = models.BooleanField(default=False)
    base_url = models.URLField(max_length=500, blank=True)
    model_name = models.CharField(max_length=150, blank=True)
    encrypted_key = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'kind'], name='unique_user_model_connection')]
