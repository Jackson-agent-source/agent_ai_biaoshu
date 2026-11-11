from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.trends.services import auto_generate


class Command(BaseCommand):
    help = "从已有候选选题生成文章草稿；仍受每天上限和 Agent 额度约束"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        user_id = options["user_id"] or settings.TREND_USER_ID
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first() if user_id else None
        if not user:
            raise CommandError("请指定有效的 --user-id 或设置 TREND_USER_ID")
        generated = auto_generate(user=user, force=True, limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"生成文章草稿 {len(generated)} 篇"))
