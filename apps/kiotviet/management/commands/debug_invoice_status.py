import json

from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order


class Command(BaseCommand):
    help = (
        "Chẩn đoán invoice_status: so sánh giá trị trong DB vs endpoint DETAIL "
        "(/invoices/code/{code}) vs endpoint LIST (/invoices) cho từng mã hóa đơn. "
        "Dùng để tìm nguồn đang set invoice_status sai (vd: void-and-recreate)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, required=True, help="ID của KiotVietChannel")
        parser.add_argument(
            "--invoice-code",
            required=True,
            help="Một hoặc nhiều mã hóa đơn, phân tách bằng dấu phẩy",
        )

    def handle(self, *args, **options):
        channel_id = int(options["channel_id"])
        codes = [c.strip() for c in str(options.get("invoice_code") or "").split(",") if c.strip()]
        if not codes:
            raise CommandError("--invoice-code rỗng")

        try:
            kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc

        self.stdout.write(
            f"Channel id={kv_channel.id} name={kv_channel.name!r} old={bool(kv_channel.old)}"
        )

        for code in codes:
            self.stdout.write("=" * 80)
            self.stdout.write(f"INVOICE CODE: {code}")
            self._dump_db(code, kv_channel)
            self._dump_detail(code, kv_channel)
            self._dump_list(code, kv_channel)

    def _dump_db(self, code: str, kv_channel: KiotVietChannel) -> None:
        orders = list(
            Order.objects.filter(deleted_at__isnull=True)
            .filter(invoice_code=code)
            .values(
                "id",
                "kiotviet_channel_id",
                "invoice_id",
                "invoice_code",
                "invoice_status",
                "status",
                "order_code",
                "old_invoice_code",
                "old_id",
                "shipping_code",
                "updated_at",
            )
        )
        self.stdout.write(f"[DB] orders with invoice_code={code}: {len(orders)}")
        for o in orders:
            o["updated_at"] = str(o.get("updated_at"))
            self.stdout.write(f"  {json.dumps(o, ensure_ascii=False)}")

    def _dump_detail(self, code: str, kv_channel: KiotVietChannel) -> None:
        from urllib.parse import quote

        response = _kiotviet_request(
            order=None,
            channel=kv_channel,
            method="GET",
            endpoint=f"/invoices/code/{quote(code, safe='')}",
            params={"includePayment": "true", "includeInvoiceDelivery": "true"},
        )
        if response.status_code != 200:
            self.stdout.write(
                self.style.WARNING(
                    f"[DETAIL] status={response.status_code} body={(response.text or '').strip()[:300]}"
                )
            )
            return
        inv = response.json() if response.content else {}
        self.stdout.write(
            f"[DETAIL /invoices/code/{code}] "
            f"id={inv.get('id')} code={inv.get('code')} "
            f"status={inv.get('status')} statusValue={inv.get('statusValue')!r}"
        )

    def _dump_list(self, code: str, kv_channel: KiotVietChannel) -> None:
        # Endpoint LIST dùng filter theo code nếu KiotViet hỗ trợ; nếu không, in cảnh báo.
        response = _kiotviet_request(
            order=None,
            channel=kv_channel,
            method="GET",
            endpoint="/invoices",
            params={"code": code, "pageSize": 20, "currentItem": 0},
        )
        if response.status_code != 200:
            self.stdout.write(
                self.style.WARNING(
                    f"[LIST] status={response.status_code} body={(response.text or '').strip()[:300]}"
                )
            )
            return
        payload = response.json() if response.content else {}
        data = payload.get("data") if isinstance(payload, dict) else None
        data = data if isinstance(data, list) else []
        matched = [
            inv
            for inv in data
            if isinstance(inv, dict) and str(inv.get("code") or "").strip() == code
        ]
        self.stdout.write(f"[LIST /invoices?code={code}] total_returned={len(data)} matched={len(matched)}")
        for inv in matched:
            self.stdout.write(
                f"  id={inv.get('id')} code={inv.get('code')} "
                f"status={inv.get('status')} statusValue={inv.get('statusValue')!r}"
            )
