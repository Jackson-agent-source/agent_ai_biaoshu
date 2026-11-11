from rest_framework import serializers

from apps.trends.categories import CATEGORIES, CATEGORY_KEYS, clean_focus_terms
from apps.trends.models import TopicCandidate, TrendFetchRun, TrendItem, TrendPreference
from apps.trends.research import MAX_FOCUS_TERMS


class TrendItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrendItem
        fields = ("id", "platform", "title", "category", "research_topic", "url", "region", "rank", "metric", "published_at", "last_seen_at")


class TopicCandidateSerializer(serializers.ModelSerializer):
    trends = TrendItemSerializer(many=True, read_only=True)
    article_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = TopicCandidate
        fields = ("id", "topic", "category", "angle", "reason", "score", "status", "error_message", "article_id", "trends", "created_at")


class TrendFetchRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrendFetchRun
        fields = ("platform", "status", "item_count", "message", "created_at")


class TrendPreferenceSerializer(serializers.ModelSerializer):
    focus_terms = serializers.ListField(child=serializers.CharField(max_length=40), min_length=1, max_length=MAX_FOCUS_TERMS)
    auto_categories = serializers.ListField(child=serializers.ChoiceField(choices=CATEGORIES), allow_empty=True)

    class Meta:
        model = TrendPreference
        fields = ("focus_terms", "auto_categories")

    def validate_focus_terms(self, value):
        try:
            return clean_focus_terms(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_auto_categories(self, value):
        if any(category not in CATEGORY_KEYS for category in value):
            raise serializers.ValidationError("包含未知分类")
        return list(dict.fromkeys(value))
