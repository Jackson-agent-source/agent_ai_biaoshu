"""账号相关的序列化器。"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """对外暴露的用户信息。注意：绝不返回 password 字段。"""

    class Meta:
        model = User
        fields = ("id", "username", "email", "is_staff", "date_joined")
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    """登录请求。"""

    username = serializers.CharField(
        max_length=150,
        error_messages={"required": "请输入用户名。", "blank": "用户名不能为空。"},
    )
    password = serializers.CharField(
        max_length=128,
        write_only=True,
        error_messages={"required": "请输入密码。", "blank": "密码不能为空。"},
    )


class RegisterSerializer(serializers.Serializer):
    """注册请求。

    V1 只做了最基本的校验，没有邮箱验证、没有邀请码 —— 本地自用足够。
    """

    username = serializers.CharField(
        max_length=150,
        min_length=3,
        error_messages={
            "required": "请输入用户名。",
            "blank": "用户名不能为空。",
            "min_length": "用户名至少 3 个字符。",
            "max_length": "用户名最多 150 个字符。",
        },
    )
    password = serializers.CharField(
        max_length=128,
        min_length=6,
        write_only=True,
        error_messages={
            "required": "请输入密码。",
            "blank": "密码不能为空。",
            "min_length": "密码至少 6 位。",
        },
    )
    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        default="",
        error_messages={"invalid": "邮箱格式不正确。"},
    )

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("这个用户名已经被占用了，换一个吧。")
        return value

    def validate_password(self, value: str) -> str:
        """复用 Django 自带的密码强度校验，不自己造规则。"""
        validate_password(value)
        return value

    def create(self, validated_data: dict):
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )
