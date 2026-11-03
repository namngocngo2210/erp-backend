"""Backfill trạng thái vận chuyển thực từ Pancake (two-way-sync §5.1, MR D).

    manage.py pancake_pos_backfill_delivery_status --days 14 --dry-run   # chỉ đếm + thử khớp
    manage.py pancake_pos_backfill_delivery_status --days 14             # ghi

Kéo đơn Pancake CẬP NHẬT trong N ngày (GET read-only, không ghi gì lên Pancake), áp qua đúng
pipeline webhook: khớp đơn CRM, ghi `partner_delivery_status` / `partner_dvvc_delivery_status`,
rồi mã "Trạng thái vận chuyển thực" — KHÔNG BAO GIỜ xuất kho/trừ tồn (kể cả khi bật
PANCAKE_POS_DISPATCH_ENABLED); đơn Pancake đã gửi mà CRM chưa xuất kho nằm ở báo cáo lệch.
Chạy lại an toàn (sự kiện trùng payload bị bỏ, áp lại cùng bản không đổi gì).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.pancake_pos.application.services import delivery_jobs
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError

MAX_DAYS = 90


class Command(BaseCommand):
    help = "Kéo trạng thái vận chuyển đơn Pancake N ngày gần nhất vào đơn CRM."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, required=True, help=f"Số ngày (1–{MAX_DAYS})")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ đọc: đếm trạng thái + thử khớp")

    def handle(self, *args, **options):
        days = int(options["days"])
        if not 1 <= days <= MAX_DAYS:
            raise CommandError(f"--days phải trong 1–{MAX_DAYS}")
        dry_run = bool(options["dry_run"])
        try:
            report = delivery_jobs.backfill(
                days=days, dry_run=dry_run,
                on_progress=lambda r: self.stdout.write(f"… {r.fetched} đơn, ghi {r.stored}"),
            )
        except PancakePosError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{'[DRY-RUN] ' if dry_run else ''}Đơn Pancake: {report.fetched}, "
                          f"lưu {report.stored}, trùng {report.duplicate}")
        for title, counter in (
            ("Kết quả xử lý/khớp", report.outcomes), ("Trạng thái vận chuyển thực", report.delivery),
            ("Trạng thái Pancake", report.statuses), ("Trạng thái ĐVVC", report.dvvc),
        ):
            if counter:
                self.stdout.write(f"{title}:")
                for key, n in counter.most_common():
                    self.stdout.write(f"  {n:>6}  {key}")
