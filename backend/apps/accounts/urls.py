"""账号路由，挂在 /api/v1/auth/ 下面。"""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login", views.LoginView.as_view(), name="login"),
    path("logout", views.LogoutView.as_view(), name="logout"),
    path("register", views.RegisterView.as_view(), name="register"),
    path("me", views.MeView.as_view(), name="me"),
]
