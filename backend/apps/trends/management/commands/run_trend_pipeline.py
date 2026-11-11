"""由 cron 或 Windows 任务计划定时执行；单进程顺序完成整个流程。"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.trends.services import auto_generate, build_topics, collect_focus_trends, collect_trends


class Command(BaseCommand):
    help = "采集已接入的热点和资讯来源、建立候选主题，并按配置自动生成文章草稿"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None, help="选题所属用户 ID；默认使用 TREND_USER_ID")
        parser.add_argument("--collect-only", action="store_true", help="只采集热点，不生成选题和文章")
        parser.add_argument("--focus-only", action="store_true", help="仅追踪接收账号的研究方向，跳过综合榜单")
        parser.add_argument("--no-generate", action="store_true", help="采集及建立选题，不自动生成文章")

    def handle(self, *args, **options):
        runs = [] if options["focus_only"] else collect_trends()
        for run in runs:
            self.stdout.write(f"{run.platform}: {run.status}, {run.item_count} 条, {run.message}")
        if options["collect_only"]:
            return
        users = self._target_users(options["user_id"])
        for user in users:
            focus_run = collect_focus_trends(user=user)
            self.stdout.write(f"{user.username} 关注方向: {focus_run.status}, {focus_run.item_count} 条, {focus_run.message}")
            candidates = build_topics(user=user)
            generated = [] if options["no_generate"] else auto_generate(user=user)
            self.stdout.write(self.style.SUCCESS(
                f"{user.username}: 候选选题 {len(candidates)} 条；生成草稿 {len(generated)} 篇"
            ))

    @staticmethod
    def _target_users(user_id):
        user_model = get_user_model()
        if user_id:
            user = user_model.objects.filter(pk=user_id, is_active=True).first()
            if not user:
                raise CommandError(f"用户 ID {user_id} 不存在或已禁用。采集数据已保存")
            return [user]
        if settings.TREND_USERNAMES:
            names = list(dict.fromkeys(settings.TREND_USERNAMES))
            users = list(user_model.objects.filter(username__in=names, is_active=True))
            by_name = {user.username: user for user in users}
            missing = [name for name in names if name not in by_name]
            if missing:
                raise CommandError(f"以下热点接收账号不存在或已禁用：{', '.join(missing)}。采集数据已保存")
            return [by_name[name] for name in names]
        if settings.TREND_USER_ID:
            user = user_model.objects.filter(pk=settings.TREND_USER_ID, is_active=True).first()
            if not user:
                raise CommandError("TREND_USER_ID 指向的用户不存在或已禁用。采集数据已保存")
            return [user]
        active = list(user_model.objects.filter(is_active=True).order_by("pk")[:2])
        if len(active) != 1:
            raise CommandError("请设置 TREND_USERNAMES；未设置时仅支持恰好一个活跃用户。采集数据已保存")
        return active
