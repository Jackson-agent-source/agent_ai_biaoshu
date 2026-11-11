from django.core.management.base import BaseCommand, CommandError

from apps.trends.providers import PROVIDERS
from apps.trends.services import collect_trends


class Command(BaseCommand):
    help = "只采集平台热点并记录状态，不生成选题或文章"

    def add_arguments(self, parser):
        parser.add_argument("--platform", action="append", choices=list(PROVIDERS), help="可重复指定；默认全部平台")

    def handle(self, *args, **options):
        runs = collect_trends(options["platform"])
        for run in runs:
            self.stdout.write(f"{run.platform}: {run.status}, {run.item_count} 条, {run.message}")
        if not any(run.status == "success" for run in runs):
            raise CommandError("没有平台成功采集热点")
