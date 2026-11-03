"""Backfill dữ liệu lịch sử sang CNV Loyalty.

Mặc định dispatch Celery task ``run_backfill`` (queue slow, single-flight lock);
``--inline`` chạy ngay trong process — dùng khi chạy tay giờ thấp điểm và muốn
thấy stats ngay. ``--dry-run`` chỉ đếm (luôn inline).
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.cnv_loyalty.application.services.backfill_service import (
    DEFAULT_ENTITIES,
    CnvBackfillService,
)


class Command(BaseCommand):
    help = "Backfill products/collections/customers/orders sang CNV Loyalty (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entities",
            default=",".join(DEFAULT_ENTITIES),
            help=f"Danh sách entity, mặc định: {','.join(DEFAULT_ENTITIES)}",
        )
        parser.add_argument("--since", default="", help="Chỉ customer/order tạo từ YYYY-MM-DD")
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không gọi API")
        parser.add_argument("--inline", action="store_true", help="Chạy ngay, không qua Celery")

    def handle(self, *args, **options):
        entities = [item.strip() for item in options["entities"].split(",") if item.strip()]
        unknown = [item for item in entities if item not in DEFAULT_ENTITIES]
        if unknown:
            raise CommandError(f"Entity lạ: {unknown} (hợp lệ: {DEFAULT_ENTITIES})")

        since = options["since"]
        if since:
            try:
                since_dt = timezone.make_aware(datetime.fromisoformat(since))
            except ValueError as exc:
                raise CommandError(f"--since không hợp lệ: {since}") from exc
        else:
            since_dt = None

        # KHÔNG gate theo CNV_SYNC_ENABLED: cờ đó dành cho luồng tự động
        # (signal/cron). Backfill là lệnh người vận hành cố ý gõ nên phải chạy
        # được cả khi sync realtime còn tắt.
        if options["dry_run"] or options["inline"]:
            from apps.cnv_loyalty.tasks import _build_service

            service = _build_service(require_enabled=False)
            if service is None:
                raise CommandError("Chưa có CnvConnection active — kết nối SSO trước.")
            stats = CnvBackfillService(service, batch_size=options["batch_size"]).run(
                entities=entities, since=since_dt, dry_run=options["dry_run"]
            )
            self.stdout.write(self.style.SUCCESS(f"cnv_backfill xong: {stats}"))
            return

        from apps.cnv_loyalty.tasks import run_backfill

        result = run_backfill.delay(
            entities=entities,
            since=since_dt.isoformat() if since_dt else None,
            batch_size=options["batch_size"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Đã dispatch run_backfill (task_id={result.id}) — theo dõi log worker slow."
            )
        )
