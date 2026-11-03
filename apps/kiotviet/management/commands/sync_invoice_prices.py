"""Kéo tiền từ hóa đơn KiotViet về đơn CRM cho một kênh (chạy tay, có dry-run).

Thay hai lệnh cũ ``sync_invoice_amounts`` + ``sync_invoice_item_prices`` (gộp
2026-09-23, audit T3/P2-5)::

    python manage.py sync_invoice_prices --kiotviet-channel-id 3 --target amounts --dry-run
    python manage.py sync_invoice_prices --kiotviet-channel-id 3 --target items --limit 50
    python manage.py sync_invoice_prices --kiotviet-channel-id 3 --target both

- ``amounts``: ``Order.total_amount`` + ``total_after_discount`` := ``Total`` hóa đơn.
- ``items``: ``OrderItem.unit_price`` := ``priceAfterTax`` (fallback ``price``) khớp
  theo SKU, ``line_total`` tính lại giữ giảm giá dòng.
- ``both``: cả hai, gọi KiotViet một lần cho mỗi đơn.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.infrastructure.services.invoice_price_sync import (
    TARGET_BOTH,
    TARGET_CHOICES,
    InvoicePriceSyncer,
)
from apps.kiotviet.models import KiotVietChannel


class Command(BaseCommand):
    help = "Sync tiền (tổng đơn / đơn giá dòng) từ hóa đơn KiotViet về đơn CRM."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kiotviet-channel-id", type=int, required=True, help="KiotVietChannel ID",
        )
        parser.add_argument(
            "--target",
            choices=TARGET_CHOICES,
            default=TARGET_BOTH,
            help="amounts = tổng tiền đơn; items = đơn giá từng dòng; both (mặc định) = cả hai.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Log changes without writing to DB",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Limit number of orders to process (0 = no limit)",
        )

    def handle(self, *args, **options):
        channel_id = int(options["kiotviet_channel_id"])
        try:
            channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"KiotVietChannel id={channel_id} not found") from exc

        syncer = InvoicePriceSyncer(
            channel,
            targets=[options["target"]],
            dry_run=bool(options.get("dry_run")),
            limit=int(options.get("limit") or 0),
            out=self.stdout.write,
            warn=lambda msg: self.stdout.write(self.style.WARNING(msg)),
            err=lambda msg: self.stderr.write(self.style.ERROR(msg)),
        )
        summary = syncer.run()
        self.stdout.write(self.style.SUCCESS("Sync invoice prices completed."))
        self.stdout.write(str(summary))
