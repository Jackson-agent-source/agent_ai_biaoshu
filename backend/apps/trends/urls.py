from django.urls import path

from apps.trends import views

app_name = "trends"

urlpatterns = [
    path("overview", views.TrendOverviewView.as_view(), name="overview"),
    path("refresh", views.TrendRefreshView.as_view(), name="refresh"),
    path("preferences", views.TrendPreferenceView.as_view(), name="preferences"),
    path("topics/<uuid:candidate_id>/generate", views.TopicGenerateView.as_view(), name="topic-generate"),
    path("topics/<uuid:candidate_id>/restore", views.TopicRestoreView.as_view(), name="topic-restore"),
]
