import json

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from apps.customers.models import Customer
from apps.customers.services import normalize_phone
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.models import KiotVietChannel


class Command(BaseCommand):
    help = "Đồng bộ danh sách khách hàng từ KiotViet theo kiotviet_channel_id."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, required=True, help="ID của KiotVietChannel")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ log, không ghi DB")

    def handle(self, *args, **options):
        channel_id = int(options["channel_id"])
        dry_run = bool(options.get("dry_run"))

        try:
            kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc

        summary = {
            "channel_id": channel_id,
            "dry_run": dry_run,
            "read": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "pages": 0,
        }

        page_size = 100
        current_item = 0

        while True:
            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "includeRemoveIds": "false",
            }
            response = _kiotviet_request(
                order=None,
                channel=kv_channel,
                method="GET",
                endpoint="/customers",
                params=params,
            )
            body = (response.text or "").strip()
            if response.status_code != 200:
                raise CommandError(
                    f"KiotViet /customers failed: status={response.status_code}, body={body}"
                )

            payload = response.json() if response.content else {}
            customers = payload.get("data", [])
            if not isinstance(customers, list):
                customers = []

            summary["pages"] += 1
            self.stdout.write(
                f"Page {summary['pages']}: currentItem={current_item}, fetched={len(customers)}"
            )

            if not customers:
                break

            for kv_customer in customers:
                summary["read"] += 1
                try:
                    result = self._upsert_customer(kv_customer, dry_run=dry_run)
                    summary[result] += 1
                except Exception as exc:
                    summary["errors"] += 1
                    cid = kv_customer.get("id", "?")
                    ccode = kv_customer.get("code", "?")
                    self.stderr.write(
                        self.style.WARNING(f"Customer failed id={cid} code={ccode}: {exc}")
                    )

            current_item += len(customers)
            if len(customers) < page_size:
                break

        self.stdout.write(self.style.SUCCESS("Sync KiotViet customers completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    @staticmethod
    def _map_gender(kv_gender) -> int | None:
        """Map KiotViet gender (True=nam, False=nữ) to local (1=nam, 0=nữ)."""
        if kv_gender is None:
            return None
        if isinstance(kv_gender, bool):
            return 1 if kv_gender else 0
        if isinstance(kv_gender, (int, float)):
            return 1 if int(kv_gender) else 0
        return None

    @classmethod
    def _upsert_customer(cls, kv_customer: dict, *, dry_run: bool) -> str:
        code = str(kv_customer.get("code") or "").strip()
        name = str(kv_customer.get("name") or "").strip()
        contact_number = str(kv_customer.get("contactNumber") or "").strip()
        gender = cls._map_gender(kv_customer.get("gender"))

        phone_normalized = normalize_phone(contact_number)

        if dry_run:
            return "created" if not phone_normalized else "updated"

        # Tìm KH theo SĐT
        customer = None
        if phone_normalized:
            customer = Customer.objects.filter(
                phone_normalized=phone_normalized, deleted_at__isnull=True
            ).first()

        if customer:
            # Update existing customer
            update_fields = []
            if code and customer.code != code:
                customer.code = code
                update_fields.append("code")
            if name and customer.full_name != name:
                customer.full_name = name
                update_fields.append("full_name")
            if contact_number and customer.phone_raw != contact_number:
                customer.phone_raw = contact_number
                update_fields.append("phone_raw")
            if gender is not None and customer.gender != gender:
                customer.gender = gender
                update_fields.append("gender")
            if update_fields:
                customer.save(update_fields=[*sorted(set(update_fields)), "updated_at"])
            return "updated"
        else:
            # Tạo KH mới. Race: tiến trình song song (import/sync khác) vừa tạo customer
            # cùng SĐT → cust_phone_norm_active_uniq chặn INSERT. Bắt và re-fetch bản
            # thắng cuộc thay vì để customer này báo failed.
            try:
                with transaction.atomic():
                    Customer.objects.create(
                        code=code,
                        full_name=name,
                        phone_raw=contact_number,
                        phone_normalized=phone_normalized,
                        gender=gender,
                    )
                return "created"
            except IntegrityError:
                existing = Customer.objects.filter(
                    phone_normalized=phone_normalized, deleted_at__isnull=True
                ).first()
                if existing is None:
                    raise
                return "updated"
