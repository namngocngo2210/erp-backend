import json

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.infrastructure.services.invoice_sync_queries import _extract_invoice_items
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order


class Command(BaseCommand):
    help = "Backfill Order.kiotviet_purchase_date từ KiotViet /invoices, mapping theo invoice_code."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, required=True, help="ID của KiotVietChannel")
        parser.add_argument(
            "--branch-ids",
            required=True,
            help="Danh sách branch ids, phân tách bằng dấu phẩy (vd: 231174,231175)",
        )
        parser.add_argument(
            "--from",
            dest="from_dt",
            default="",
            help='Lọc theo purchaseDate từ, vd: "2026-04-01" hoặc "2026-04-01 00:00:00"',
        )
        parser.add_argument(
            "--to",
            dest="to_dt",
            default="",
            help='Lọc theo purchaseDate đến, vd: "2026-04-30" hoặc "2026-04-30 23:59:59"',
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Ghi đè kể cả khi order đã có kiotviet_purchase_date (mặc định chỉ set khi NULL)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Chỉ log, không ghi DB")

    def handle(self, *args, **options):
        channel_id = int(options["channel_id"])
        branch_ids = [item.strip() for item in str(options["branch_ids"] or "").split(",") if item.strip()]
        from_dt = self._normalize_dt_arg(options.get("from_dt"))
        to_dt = self._normalize_dt_arg(options.get("to_dt"))
        overwrite = bool(options.get("overwrite"))
        dry_run = bool(options.get("dry_run"))

        if not branch_ids:
            raise CommandError("--branch-ids không hợp lệ hoặc rỗng")

        try:
            kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc

        summary = {
            "channel_id": channel_id,
            "branch_ids": branch_ids,
            "dry_run": dry_run,
            "overwrite": overwrite,
            "read": 0,
            "matched": 0,
            "updated": 0,
            "skipped_no_order": 0,
            "skipped_already_set": 0,
            "skipped_no_purchase_date": 0,
            "pages": 0,
        }

        page_size = 100
        current_item = 0

        while True:
            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "branchIds": ",".join(branch_ids),
            }
            if from_dt:
                params["fromPurchaseDate"] = from_dt
            if to_dt:
                params["toPurchaseDate"] = to_dt

            response = _kiotviet_request(
                order=None,
                channel=kv_channel,
                method="GET",
                endpoint="/invoices",
                params=params,
            )
            if response.status_code != 200:
                raise CommandError(
                    f"KiotViet /invoices failed: status={response.status_code}, body={(response.text or '').strip()}"
                )

            payload = response.json() if response.content else {}
            invoices = _extract_invoice_items(payload)
            summary["pages"] += 1
            self.stdout.write(
                f"Page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
            )
            if not invoices:
                break

            for inv in invoices:
                summary["read"] += 1
                invoice_code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
                if not invoice_code:
                    continue

                purchase_raw = self._first_non_empty(inv.get("purchaseDate"), inv.get("PurchaseDate"))
                purchase_dt = parse_datetime(purchase_raw) if purchase_raw else None
                if purchase_dt is None:
                    summary["skipped_no_purchase_date"] += 1
                    continue

                order = Order.objects.filter(
                    kiotviet_channel=kv_channel,
                    invoice_code=invoice_code,
                    deleted_at__isnull=True,
                ).first()
                if order is None:
                    summary["skipped_no_order"] += 1
                    continue

                summary["matched"] += 1
                if order.kiotviet_purchase_date is not None and not overwrite:
                    summary["skipped_already_set"] += 1
                    continue

                if order.kiotviet_purchase_date == purchase_dt:
                    summary["skipped_already_set"] += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN {invoice_code}: {order.kiotviet_purchase_date} -> {purchase_dt}"
                    )
                else:
                    order.kiotviet_purchase_date = purchase_dt
                    order.save(update_fields=["kiotviet_purchase_date", "updated_at"])
                summary["updated"] += 1

            current_item += len(invoices)
            if len(invoices) < page_size:
                break

        self.stdout.write(self.style.SUCCESS("Backfill kiotviet_purchase_date completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    @staticmethod
    def _normalize_dt_arg(value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        parsed = parse_datetime(text)
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        if len(text) == 10:
            return f"{text} 00:00:00"
        return text

    @staticmethod
    def _first_non_empty(*values):
        for value in values:
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text
        return ""
