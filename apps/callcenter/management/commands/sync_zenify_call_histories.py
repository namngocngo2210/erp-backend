"""Backfill lịch sử cuộc gọi Zenify cho khoảng ngày tuỳ chọn (chạy tay).

Ví dụ:
    python manage.py sync_zenify_call_histories --from "2026-07-01" --to "2026-07-10"
    python manage.py sync_zenify_call_histories --dry-run
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.callcenter import services
from apps.callcenter.sync_service import ZENIFY_TZ, sync_call_histories


def _parse_arg_datetime(raw: str, *, end_of_day: bool = False) -> datetime:
    text = str(raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            parsed = parsed.replace(hour=23, minute=59)
        return parsed.replace(tzinfo=ZENIFY_TZ)
    raise CommandError(f"Không parse được thời gian: {raw!r} (định dạng YYYY-MM-DD hoặc YYYY-MM-DD HH:MM)")


class Command(BaseCommand):
    help = "Kéo lịch sử cuộc gọi Zenify → callcenter_call_records + khớp đơn (backfill tay)."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="from_str", help="Từ thời điểm (giờ VN). Mặc định theo cửa sổ cron.")
        parser.add_argument("--to", dest="to_str", help="Đến thời điểm (giờ VN). Mặc định now.")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không ghi DB.")

    def handle(self, *args, **options):
        if not services.zenify_configured():
            raise CommandError("Zenify chưa cấu hình (ZENIFY_DOMAIN/ZENIFY_API_KEY/ZENIFY_API_SECRET).")

        from_dt = _parse_arg_datetime(options["from_str"]) if options.get("from_str") else None
        to_dt = _parse_arg_datetime(options["to_str"], end_of_day=True) if options.get("to_str") else None

        summary = sync_call_histories(from_dt=from_dt, to_dt=to_dt, dry_run=options["dry_run"])
        prefix = "DRY-RUN " if options["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}{summary}"))
