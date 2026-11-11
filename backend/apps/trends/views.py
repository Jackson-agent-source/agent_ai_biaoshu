from datetime import timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.common.exceptions import AppError
from apps.common.responses import success_response
from apps.trends.categories import CATEGORIES
from apps.trends.models import TopicCandidate, TrendFetchRun, TrendItem, TrendPreference
from apps.trends.providers import SOURCE_LABELS
from apps.trends.research import MAX_FOCUS_TERMS, RESEARCH_PRESETS
from apps.trends.tracking import read_status
from apps.trends.serializers import TopicCandidateSerializer, TrendFetchRunSerializer, TrendPreferenceSerializer
from apps.trends.services import build_topics, collect_focus_trends, collect_trends, generate_candidate


def _overview(user, runs=None):
    candidates = TopicCandidate.objects.filter(user=user).order_by("-created_at", "-score").prefetch_related("trends")[:100]
    if runs is None:
        runs = []
        for platform in SOURCE_LABELS:
            query = TrendFetchRun.objects.filter(platform=platform)
            query = query.filter(user=user) if platform == "focus_news" else query.filter(user__isnull=True)
            run = query.first()
            if run:
                runs.append(run)
    preference, _created = TrendPreference.objects.get_or_create(user=user)
    return {
        "candidates": TopicCandidateSerializer(candidates, many=True).data,
        "sources": TrendFetchRunSerializer(runs, many=True).data,
        "preferences": TrendPreferenceSerializer(preference).data,
        "research_presets": RESEARCH_PRESETS,
        "max_focus_terms": MAX_FOCUS_TERMS,
        "tracking": read_status(user.pk),
        "automation": {
            "enabled": settings.TREND_AUTO_GENERATE,
            "daily_article_limit": settings.TREND_DAILY_ARTICLE_LIMIT,
        },
        "categories": [{"key": key, "label": label} for key, label in CATEGORIES],
        "source_catalog": [{"key": key, "label": label} for key, label in SOURCE_LABELS.items()],
    }


class TrendOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request):
        return success_response(_overview(request.user))


class TrendRefreshView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "trend_refresh"

    def post(self, request: Request):
        runs = collect_trends()
        runs.insert(0, collect_focus_trends(user=request.user))
        build_topics(user=request.user)
        return success_response(_overview(request.user, runs))


class TrendPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request: Request):
        preference, _created = TrendPreference.objects.get_or_create(user=request.user)
        serializer = TrendPreferenceSerializer(preference, data=request.data)
        serializer.is_valid(raise_exception=True)
        old_terms = preference.focus_terms
        serializer.save()
        if old_terms != preference.focus_terms:
            outdated = TrendItem.objects.filter(owner=request.user, category="focus").exclude(
                research_topic__in=preference.focus_terms,
            )
            TopicCandidate.objects.filter(
                user=request.user, category="focus",
                status__in=[TopicCandidate.Status.READY, TopicCandidate.Status.FAILED],
                trends__in=outdated,
            ).exclude(trends__research_topic__in=preference.focus_terms).update(
                status=TopicCandidate.Status.SKIPPED,
                error_message="研究方向已调整，旧选题已暂存；仍感兴趣时可以恢复选题。",
                updated_at=timezone.now(),
            )
            outdated.update(
                last_seen_at=timezone.now() - timedelta(days=3),
            )
        return success_response(TrendPreferenceSerializer(preference).data)


class TopicRestoreView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, candidate_id):
        candidate = get_object_or_404(TopicCandidate, pk=candidate_id, user=request.user)
        restored = TopicCandidate.objects.filter(
            pk=candidate.pk, user=request.user, status=TopicCandidate.Status.SKIPPED, article__isnull=True,
        ).update(status=TopicCandidate.Status.READY, error_message="", updated_at=timezone.now())
        if not restored:
            raise AppError("只有暂存且尚未生成文章的选题可以恢复。", code="TOPIC_NOT_RESTORABLE")
        candidate.refresh_from_db()
        return success_response(TopicCandidateSerializer(candidate).data)


class TopicGenerateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "agent_generate"

    def post(self, request: Request, candidate_id):
        candidate = get_object_or_404(TopicCandidate, pk=candidate_id, user=request.user)
        try:
            article = generate_candidate(candidate=candidate, user=request.user)
        except ValueError as exc:
            raise AppError(str(exc), code="TOPIC_ALREADY_USED") from exc
        return success_response({"article_id": str(article.pk), "candidate_id": str(candidate.pk)})
