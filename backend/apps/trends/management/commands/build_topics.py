from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.trends.services import build_topics


class Command(BaseCommand):
    help = "根据最近 48 小时的热点，为指定用户生成候选选题"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        user_id = options["user_id"] or settings.TREND_USER_ID
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first() if user_id else None
        if not user:
            raise CommandError("请指定有效的 --user-id 或设置 TREND_USER_ID")
        candidates = build_topics(user=user, limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"生成或更新 {len(candidates)} 个候选选题"))
