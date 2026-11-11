"""账号路由，挂在 /api/v1/auth/ 下面。"""

from django.urls import path

from apps.accounts import views
from apps.accounts.connection_views import ConnectionsView, ConnectionView, ConnectionTestView

app_name = "accounts"

urlpatterns = [
    path('api-settings', ConnectionsView.as_view(), name='api-settings'),
    path('api-settings/<str:kind>', ConnectionView.as_view(), name='api-connection'),
    path('api-settings/<str:kind>/test', ConnectionTestView.as_view(), name='api-connection-test'),
    path("login", views.LoginView.as_view(), name="login"),
    path("logout", views.LogoutView.as_view(), name="logout"),
    path("register", views.RegisterView.as_view(), name="register"),
    path("me", views.MeView.as_view(), name="me"),
]
