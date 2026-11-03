"""Kéo lịch sử đơn Foxia theo khoảng ngày tuỳ ý.

Dùng cho hai việc: đổ dữ liệu lịch sử lần đầu, và vá tay khi có đơn trôi khỏi
cửa sổ quét tự động (Foxia không cấp `write_date` nên cron chỉ nhìn được vài
ngày gần nhất — xem docs/foxia-integration-design.md §6.3).

    manage.py foxia_backfill --from 2026-01-01 --to 2026-06-30
    manage.py foxia_backfill --from 2026-01-01 --to 2026-06-30 --mode delivered
    manage.py foxia_backfill --from 2026-01-01 --to 2026-06-30 --chunk-days 7

Chạy được cả khi FOXIA_SYNC_ENABLED=0: đây là lệnh người vận hành cố ý gõ, cờ
env chỉ gate luồng tự động.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.foxia.domain.order_specs import OrderSyncMode
from apps.foxia.exceptions import FoxiaError


class Command(BaseCommand):
    help = "Backfill đơn Foxia theo khoảng ngày"

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
        parser.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
        parser.add_argument(
            "--mode",
            choices=["confirmed", "delivered"],
            default="confirmed",
            help="confirmed = lọc theo ngayxacnhan; delivered = theo ngayhoanthanh",
        )
        parser.add_argument(
            "--chunk-days",
            type=int,
            default=30,
            help=(
                "Cắt khoảng lớn thành từng lát. Lát nhỏ giữ mỗi lượt dưới trần "
                "500 bản ghi/trang và cho phép dừng giữa chừng mà không mất phần đã làm."
            ),
        )

    def handle(self, *args, **options):
        from apps.foxia.application.services.order_sync_service import FoxiaOrderSyncService
        from apps.foxia.tasks import build_client

        date_from = _parse_day(options["date_from"])
        date_to = _parse_day(options["date_to"], end_of_day=True)
        if date_from > date_to:
            raise CommandError("--from phải trước --to")

        mode = (
            OrderSyncMode.CONFIRMED
            if options["mode"] == "confirmed"
            else OrderSyncMode.DELIVERED
        )
        chunk = max(1, options["chunk_days"])

        client = build_client(require_enabled=False)
        if client is None:
            raise CommandError("Chưa có FoxiaConnection active — tạo ở Django admin trước.")

        service = FoxiaOrderSyncService(client)
        total_created = total_updated = 0
        cursor = date_from
        while cursor <= date_to:
            chunk_end = min(cursor + timedelta(days=chunk) - timedelta(seconds=1), date_to)
            self.stdout.write(f"→ {cursor:%Y-%m-%d} … {chunk_end:%Y-%m-%d}")
            try:
                result = service.sync_window(mode, cursor, chunk_end)
            except FoxiaError as exc:
                # Dừng hẳn thay vì bỏ qua lát lỗi: backfill có lỗ thủng ở giữa là
                # thứ không ai phát hiện được sau này. Chạy lại từ mốc lỗi được vì
                # upsert idempotent theo foxia_id.
                raise CommandError(f"Lỗi ở lát {cursor:%Y-%m-%d}: {exc}") from exc
            total_created += result.created
            total_updated += result.updated
            self.stdout.write(
                f"   {result.fetched} bản ghi — tạo {result.created}, cập nhật {result.updated}"
            )
            cursor = chunk_end + timedelta(seconds=1)

        self.stdout.write(
            self.style.SUCCESS(f"Xong: tạo {total_created}, cập nhật {total_updated}")
        )


def _parse_day(raw: str, end_of_day: bool = False):
    try:
        value = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise CommandError(f"Ngày không hợp lệ: {raw} (cần YYYY-MM-DD)") from exc
    if end_of_day:
        value = value.replace(hour=23, minute=59, second=59)
    # Gắn múi giờ hệ thống (+07) rồi để tầng client đổi sang UTC — người vận hành
    # gõ ngày theo giờ Việt Nam, không phải giờ UTC.
    return timezone.make_aware(value)
