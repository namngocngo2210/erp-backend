"""Service import hóa đơn KiotViet — business logic move NGUYÊN VĂN từ
management command ``import_kiotviet_invoices`` (WP8.1 audit hardening).

- Command chỉ còn parse args rồi gọi ``ImportInvoicesService.run(**options)``.
- Celery tasks / webhook gọi các method CÔNG KHAI (``import_invoices``,
  ``update_status_phone``, ``upsert_invoice``) thay vì mượn private của Command.
- Giữ nguyên ``CommandError`` cho các lỗi vận hành để hành vi CLI/task không đổi
  (BaseCommand in gọn message; task trước đây cũng nhận đúng exception này).
"""

import json
from decimal import Decimal, InvalidOperation

from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.channels_core.models import Channel
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.infrastructure.services.invoice_sync_queries import _extract_invoice_items
from apps.kiotviet.models import KiotVietChannel
from apps.orders.address_catalog import map_to_catalog
from apps.orders.address_parser import parse_legacy_level3
from apps.orders.models import Order, OrderStatus

from ._import_service_handlers_mixin import ImportHandlersMixin
from ._import_service_upsert_mixin import ImportUpsertMixin
from ._service_output import PlainStyle, StreamOutput


class ImportInvoicesService(ImportHandlersMixin, ImportUpsertMixin):
    """Import full KiotViet invoices by pagination and upsert to orders."""

    def __init__(self, *, stdout=None, stderr=None, style=None):
        self.stdout = stdout if stdout is not None else StreamOutput()
        self.stderr = stderr if stderr is not None else StreamOutput()
        self.style = style if style is not None else PlainStyle()

    # ------------------------------------------------------------------
    # Public entrypoints (Celery tasks / webhook / command)
    # ------------------------------------------------------------------
    def import_invoices(self, **kwargs) -> dict:
        """Public wrapper cho vòng lặp import chính (cron create-only + CLI)."""
        return self._handle_import(**kwargs)

    def update_status_phone(self, **kwargs):
        """Public wrapper cho update invoice_status/status/phone (cron + CLI)."""
        return self._handle_update_status_phone(**kwargs)

    @classmethod
    def upsert_invoice(cls, *, invoice: dict, kv_channel: KiotVietChannel, dry_run: bool, create_only: bool = False) -> str:
        """Public wrapper cho upsert 1 invoice (webhook auto-create dùng chung)."""
        return cls._upsert_invoice(
            invoice=invoice,
            kv_channel=kv_channel,
            dry_run=dry_run,
            create_only=create_only,
        )

    # ------------------------------------------------------------------
    # Full-option run (CLI entry) — body move nguyên văn từ Command.handle()
    # ------------------------------------------------------------------
    def run(self, **options):
        channel_id = int(options["channel_id"])
        branch_ids = self._parse_branch_ids(options.get("branch_ids"))
        dry_run = bool(options.get("dry_run"))
        create_only = bool(options.get("create_only"))
        list_missing = bool(options.get("list_missing"))
        diff_mode = bool(options.get("diff"))
        update_status = bool(options.get("update_status"))
        update_phone = bool(options.get("update_phone"))
        resync_codes = bool(options.get("resync_codes"))
        check_channel_match = bool(options.get("check_channel_match"))
        sale_channel_db_id = int(options.get("sale_channel_id") or 0) or None
        missing_invoices: list[dict] = []
        invoice_code_filter = str(options.get("invoice_code") or "").strip()
        invoice_codes = [code.strip() for code in invoice_code_filter.split(",") if code.strip()]
        from_dt = self._normalize_dt_arg(options.get("from_dt"))
        to_dt = self._normalize_dt_arg(options.get("to_dt"))

        if not branch_ids and not invoice_codes:
            raise CommandError("--branch-ids không hợp lệ hoặc rỗng")

        try:
            kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc

        target_sale_channel_value = ""
        if sale_channel_db_id:
            try:
                target_channel = Channel.objects.get(id=sale_channel_db_id, deleted_at__isnull=True)
            except Channel.DoesNotExist as exc:
                raise CommandError(f"Không tìm thấy Channel id={sale_channel_db_id}") from exc
            from apps.channels_core.models import ChannelKiotvietMapping
            mapping = (
                ChannelKiotvietMapping.objects
                .filter(channel=target_channel, kiotviet_channel=kv_channel)
                .only("external_sale_channel_id")
                .first()
            )
            target_sale_channel_value = str(mapping.external_sale_channel_id if mapping else "").strip()
            if not target_sale_channel_value:
                raise CommandError(
                    f"Channel id={sale_channel_db_id} chưa có mapping sang KiotVietChannel id={kv_channel.id}."
                )
            self.stdout.write(
                f"--sale-channel-id={sale_channel_db_id} → KV saleChannelId='{target_sale_channel_value}'"
            )

        summary = {
            "channel_id": channel_id,
            "branch_ids": branch_ids,
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

        if diff_mode:
            if not branch_ids:
                raise CommandError("--diff yêu cầu --branch-ids")
            kv_codes: set[str] = set()
            pages = 0
            while True:
                params = {
                    "pageSize": page_size,
                    "currentItem": current_item,
                    "branchIds": ",".join(branch_ids),
                    # Sort id/Asc để phân trang offset ổn định (default modifiedDate
                    # xê dịch giữa trang → diff sai vừa thừa vừa thiếu).
                    "orderBy": "id",
                    "orderDirection": "Asc",
                }
                self._apply_date_params(params, from_dt, to_dt)
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
                pages += 1
                self.stdout.write(f"Diff page {pages}: currentItem={current_item}, fetched={len(invoices)}")
                if not invoices:
                    break
                for inv in invoices:
                    if self._resolve_invoice_own_status(invoice=inv) == 2:
                        continue
                    if target_sale_channel_value and self._extract_sale_channel_id(inv) != target_sale_channel_value:
                        continue
                    code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
                    if code:
                        kv_codes.add(code)
                current_item += len(invoices)
                if len(invoices) < page_size:
                    break

            db_qs = (
                Order.objects.filter(
                    kiotviet_channel=kv_channel,
                    deleted_at__isnull=True,
                )
                .exclude(invoice_code="")
                .exclude(invoice_status=2)
            )
            if branch_ids:
                db_qs = db_qs.filter(branch_id__in=branch_ids)
            if sale_channel_db_id:
                db_qs = db_qs.filter(channel_id=sale_channel_db_id)
            if from_dt:
                db_qs = db_qs.filter(kiotviet_purchase_date__gte=parse_datetime(from_dt))
            if to_dt:
                db_qs = db_qs.filter(kiotviet_purchase_date__lte=parse_datetime(to_dt))
            db_codes = set(db_qs.values_list("invoice_code", flat=True))
            self.stdout.write(
                f"DB lookup channel: {kv_channel.id}, branches: {branch_ids}, "
                f"date_range: [{from_dt or '*'} .. {to_dt or '*'}]"
            )
            only_in_db = sorted(db_codes - kv_codes)
            only_in_kv = sorted(kv_codes - db_codes)
            self.stdout.write(self.style.SUCCESS("KiotViet diff completed."))
            self.stdout.write(json.dumps({
                "kv_total": len(kv_codes),
                "db_total": len(db_codes),
                "only_in_db_total": len(only_in_db),
                "only_in_kv_total": len(only_in_kv),
            }, ensure_ascii=False))
            self.stdout.write(f"ONLY_IN_DB ({len(only_in_db)}):")
            self.stdout.write(json.dumps(only_in_db, ensure_ascii=False))
            self.stdout.write(f"ONLY_IN_KV ({len(only_in_kv)}):")
            self.stdout.write(json.dumps(only_in_kv, ensure_ascii=False))
            return

        if resync_codes:
            if not branch_ids and not invoice_codes:
                raise CommandError("--resync-codes yêu cầu --branch-ids hoặc --invoice-code")
            self._handle_resync_codes(
                kv_channel=kv_channel,
                branch_ids=branch_ids,
                invoice_codes=invoice_codes,
                dry_run=dry_run,
                from_dt=from_dt,
                to_dt=to_dt,
                target_sale_channel_value=target_sale_channel_value,
            )
            return

        if check_channel_match:
            if not branch_ids and not invoice_codes:
                raise CommandError("--check-channel-match yêu cầu --branch-ids hoặc --invoice-code")
            self._handle_check_channel_match(
                kv_channel=kv_channel,
                branch_ids=branch_ids,
                invoice_codes=invoice_codes,
                dry_run=dry_run,
                from_dt=from_dt,
                to_dt=to_dt,
                target_sale_channel_value=target_sale_channel_value,
                only_current_channel_ids=self._parse_channel_ids(
                    options.get("only_current_channel_ids")
                ),
                empty_sale_channel_to=self._resolve_channel_arg(
                    options.get("empty_sale_channel_to"),
                    label="--empty-sale-channel-to",
                ),
            )
            return

        if update_status or update_phone:
            if not branch_ids and not invoice_codes:
                raise CommandError("--update-status/--update-phone yêu cầu --branch-ids hoặc --invoice-code")
            self._handle_update_status_phone(
                kv_channel=kv_channel,
                branch_ids=branch_ids,
                invoice_codes=invoice_codes,
                dry_run=dry_run,
                do_status=update_status,
                do_phone=update_phone,
                from_dt=from_dt,
                to_dt=to_dt,
                target_sale_channel_value=target_sale_channel_value,
            )
            return

        if invoice_codes:
            from urllib.parse import quote

            summary["pages"] = len(invoice_codes)
            for code in invoice_codes:
                response = _kiotviet_request(
                    order=None,
                    channel=kv_channel,
                    method="GET",
                    endpoint=f"/invoices/code/{quote(code, safe='')}",
                    params={"includePayment": "true", "includeInvoiceDelivery": "true"},
                )
                body = (response.text or "").strip()
                if response.status_code != 200:
                    summary["errors"] += 1
                    self.stderr.write(
                        self.style.WARNING(
                            f"KiotViet /invoices/code failed code={code}: status={response.status_code}, body={body}"
                        )
                    )
                    continue
                invoice = response.json() if response.content else {}
                invoices = [invoice] if isinstance(invoice, dict) and invoice else []
                self.stdout.write(f"Single invoice fetch: code={code}, found={len(invoices)}")
                for inv in invoices:
                    summary["read"] += 1
                    if target_sale_channel_value and self._extract_sale_channel_id(inv) != target_sale_channel_value:
                        summary["skipped"] += 1
                        continue
                    try:
                        if list_missing:
                            existing = self._find_existing_order(inv, kv_channel)
                            if existing is None:
                                inv_code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
                                inv_id = self._to_int(self._first_non_empty(inv.get("id"), inv.get("invoiceId")))
                                missing_invoices.append({"id": inv_id, "code": inv_code})
                                summary["created"] += 1
                                self.stdout.write(f"MISSING id={inv_id} code={inv_code}")
                            else:
                                summary["skipped"] += 1
                        else:
                            result = self._upsert_invoice(
                                invoice=inv,
                                kv_channel=kv_channel,
                                dry_run=dry_run,
                                create_only=create_only,
                            )
                            summary[result] += 1
                    except Exception as exc:
                        summary["errors"] += 1
                        self.stderr.write(self.style.WARNING(f"Invoice failed code={code}: {exc}"))
            self.stdout.write(self.style.SUCCESS("Import KiotViet invoices completed."))
            self.stdout.write(json.dumps(summary, ensure_ascii=False))
            if list_missing:
                self.stdout.write(self.style.SUCCESS(f"Missing total: {len(missing_invoices)}"))
                self.stdout.write(json.dumps(missing_invoices, ensure_ascii=False))
            return

        self._handle_import(
            kv_channel=kv_channel,
            branch_ids=branch_ids,
            dry_run=dry_run,
            create_only=create_only,
            list_missing=list_missing,
            from_dt=from_dt,
            to_dt=to_dt,
            target_sale_channel_value=target_sale_channel_value,
        )

    # ------------------------------------------------------------------
    # Helpers — move nguyên văn từ Command (static/classmethod)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_branch_ids(raw: str | None) -> list[str]:
        return [item.strip() for item in str(raw or "").split(",") if item.strip()]

    @staticmethod
    def _parse_channel_ids(raw: str | None) -> list[int]:
        """Parse csv Channel.id; báo lỗi sớm thay vì im lặng bỏ qua giá trị rác."""
        ids: list[int] = []
        for item in str(raw or "").split(","):
            text = item.strip()
            if not text:
                continue
            if not text.lstrip("-").isdigit():
                raise CommandError(f"--only-current-channel-ids không hợp lệ: '{text}'")
            ids.append(int(text))
        return ids

    @staticmethod
    def _resolve_channel_arg(raw, *, label: str) -> Channel | None:
        channel_id = int(raw or 0)
        if not channel_id:
            return None
        try:
            return Channel.objects.get(id=channel_id, deleted_at__isnull=True)
        except Channel.DoesNotExist as exc:
            raise CommandError(f"{label}: không tìm thấy Channel id={channel_id}") from exc

    @staticmethod
    def _normalize_dt_arg(value) -> str:
        """Normalize datetime arg: '2026-04-01' → '2026-04-01 00:00:00', ISO → 'YYYY-MM-DD HH:MM:SS'."""
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
    def _apply_date_params(params: dict, from_dt: str, to_dt: str) -> None:
        """Inject fromPurchaseDate/toPurchaseDate vào params dict nếu có."""
        if from_dt:
            params["fromPurchaseDate"] = from_dt
        if to_dt:
            params["toPurchaseDate"] = to_dt

    @staticmethod
    def _first_non_empty(*values):
        for value in values:
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        if value in (None, ""):
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_dt(value):
        if value in (None, ""):
            return None
        parsed = parse_datetime(str(value))
        # KiotViet trả giờ VN không kèm offset → naive. USE_TZ=True nên phải gắn
        # timezone hiện tại (Asia/Bangkok, +07) để tránh RuntimeWarning và lệch 7h.
        if parsed is not None and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    @staticmethod
    def _format_dim_value(value) -> str:
        if value in (None, ""):
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            decimal_value = Decimal(text)
            normalized = decimal_value.normalize()
            normalized_text = format(normalized, "f")
            if "." in normalized_text:
                normalized_text = normalized_text.rstrip("0").rstrip(".")
            return normalized_text
        except (InvalidOperation, ValueError):
            return text

    @classmethod
    def _build_dimensions(cls, delivery: dict, invoice: dict) -> str:
        length = cls._first_non_empty(delivery.get("length"), delivery.get("Length"), invoice.get("length"), invoice.get("Length"))
        width = cls._first_non_empty(delivery.get("width"), delivery.get("Width"), invoice.get("width"), invoice.get("Width"))
        height = cls._first_non_empty(delivery.get("height"), delivery.get("Height"), invoice.get("height"), invoice.get("Height"))
        if not (length and width and height):
            return ""
        return "x".join([cls._format_dim_value(length), cls._format_dim_value(width), cls._format_dim_value(height)])

    @classmethod
    def _extract_address_fields(cls, delivery: dict) -> dict:
        base_address = cls._first_non_empty(delivery.get("address"), delivery.get("Address"))
        ward_name = cls._first_non_empty(delivery.get("wardName"), delivery.get("WardName"))
        location_name = cls._first_non_empty(delivery.get("locationName"), delivery.get("LocationName"))

        joined = ", ".join([item for item in [base_address, ward_name, location_name] if item])
        parsed = parse_legacy_level3(joined)
        mapped = map_to_catalog(
            {
                "address": parsed.get("address", ""),
                "ward": parsed.get("ward", ""),
                "district": parsed.get("district", ""),
                "province": parsed.get("province", ""),
                "ward_code": parsed.get("ward_code", ""),
                "district_code": parsed.get("district_code", ""),
                "province_code": parsed.get("province_code", ""),
            }
        )

        # Resolve KiotViet location/ward for the pre-resolved fields
        from apps.orders.address_level2 import resolve_level2_fields
        from apps.orders.utils.kiotviet_address_mapping import resolve_kiotviet_address
        kv_resolved = resolve_kiotviet_address(parsed, raw_address=joined)
        # Địa chỉ 2 cấp chỉ để hiển thị: hoá đơn đã có sẵn trên Kiot nên Khu vực/
        # Phường xã giữ theo hoá đơn, không ép về cấu trúc mới.
        level2_fields = resolve_level2_fields(joined, parsed)

        return {
            "address": base_address or parsed.get("address", ""),
            "ward": mapped.get("ward") or parsed.get("ward") or ward_name,
            "district": mapped.get("district") or parsed.get("district") or "",
            "province": mapped.get("province") or parsed.get("province") or location_name,
            "ward_code": mapped.get("ward_code") or parsed.get("ward_code") or "",
            "district_code": mapped.get("district_code") or parsed.get("district_code") or "",
            "province_code": mapped.get("province_code") or parsed.get("province_code") or "",
            "kiotviet_location": kv_resolved.get("locationName", "") or location_name or "",
            "kiotviet_ward": kv_resolved.get("wardName", "") or ward_name or "",
            **level2_fields,
        }

    @staticmethod
    def _get_or_create_order_channel(kv_channel: KiotVietChannel) -> Channel:
        code = f"kiotviet-{kv_channel.id}"
        defaults = {
            "name": f"KiotViet {kv_channel.name or kv_channel.id}",
            "owner": "kiotviet",
            "landingpage_source": [],
        }
        channel, _ = Channel.objects.get_or_create(code=code, defaults=defaults)
        return channel

    @classmethod
    def _extract_sale_channel_id(cls, invoice: dict) -> str:
        direct = cls._first_non_empty(invoice.get("saleChannelId"), invoice.get("SaleChannelId"))
        if direct:
            return str(direct)

        sale_channel_obj = invoice.get("saleChannel") if isinstance(invoice.get("saleChannel"), dict) else {}
        if not sale_channel_obj:
            sale_channel_obj = invoice.get("SaleChannel") if isinstance(invoice.get("SaleChannel"), dict) else {}

        nested = cls._first_non_empty(
            sale_channel_obj.get("id"),
            sale_channel_obj.get("Id"),
            sale_channel_obj.get("channelId"),
            sale_channel_obj.get("ChannelId"),
        )
        if nested:
            return str(nested)

        return ""

    @classmethod
    def _resolve_order_channel_from_sale_channel_id(
        cls, *, kv_channel: KiotVietChannel, sale_channel_id: str
    ) -> Channel | None:
        sale_channel_text = str(sale_channel_id or "").strip()
        if not sale_channel_text:
            return None

        from apps.channels_core.models import ChannelKiotvietMapping
        mapping = (
            ChannelKiotvietMapping.objects
            .filter(
                kiotviet_channel=kv_channel,
                external_sale_channel_id=sale_channel_text,
                channel__deleted_at__isnull=True,
            )
            .select_related("channel")
            .first()
        )
        return mapping.channel if mapping else None

    @classmethod
    def _resolve_invoice_own_status(cls, *, invoice: dict) -> int | None:
        status_raw = cls._first_non_empty(
            invoice.get("status"),
            invoice.get("Status"),
            invoice.get("invoiceStatus"),
            invoice.get("InvoiceStatus"),
            invoice.get("statusValue"),
            invoice.get("StatusValue"),
        )
        return cls._to_int(status_raw)

    @classmethod
    def _resolve_delivery_status(cls, *, delivery: dict) -> int | None:
        status_raw = cls._first_non_empty(
            delivery.get("status"),
            delivery.get("Status"),
        )
        return cls._to_int(status_raw)

    @classmethod
    def _resolve_invoice_status(cls, *, invoice: dict, delivery: dict) -> int | None:
        # Ưu tiên delivery status vì đây là trạng thái giao vận được đồng bộ về Order.status.
        delivery_status = cls._to_int(
            cls._first_non_empty(delivery.get("status"), delivery.get("Status"))
        )
        if delivery_status is not None:
            return delivery_status

        invoice_own_status = cls._resolve_invoice_own_status(invoice=invoice)
        # POS sale: invoice hoàn thành (status=1) mà không có invoiceDelivery.status
        # → coi như đã giao (status=3) vì không có vận đơn để theo dõi.
        if invoice_own_status == 1:
            return 3
        # Invoice đã huỷ (status=2) mà không có invoiceDelivery.status → map sang
        # OrderStatus.CANCELLED; fallback trả nguyên 2 sẽ thành "In delivery" theo
        # thang Order.status.
        if invoice_own_status == 2:
            return int(OrderStatus.CANCELLED)

        # CHỈ đọc field thang-giao-vận. `statusValue`/`StatusValue` là THANG HOÁ ĐƠN
        # (chính _resolve_invoice_own_status đọc nó) — để trong fallback này thì payload
        # nào mang statusValue dạng số lại leak thang hoá đơn vào Order.status
        # (audit 2026-07-14 vòng 5).
        status_raw = cls._first_non_empty(
            invoice.get("deliveryStatus"),
            invoice.get("DeliveryStatus"),
        )
        fallback_status = cls._to_int(status_raw)
        if fallback_status is not None:
            return fallback_status
        # KHÔNG trả invoice_own_status thô: đó là THANG HOÁ ĐƠN (1=hoàn thành, 2=huỷ,
        # 3=đang xử lý...), không phải thang Order.status — trả thô thì hoá đơn "đang
        # xử lý"(3) thành đơn "Đã giao"(3). Hai ca có nghĩa (1, 2) đã map ở trên;
        # còn lại không suy được → None để caller giữ nguyên/không đổi trạng thái
        # (audit 2026-07-14 vòng 4).
        return None

    @classmethod
    def _resolve_staff(cls, sold_by_id, kv_channel: KiotVietChannel):
        sold_by_int = cls._to_int(sold_by_id)
        if sold_by_int is None:
            return None

        from apps.users.models import UserKiotvietMapping
        mapping = (
            UserKiotvietMapping.objects
            .filter(
                kiotviet_channel=kv_channel,
                external_kiot_user_id=str(sold_by_int),
                user__deleted_at__isnull=True,
            )
            .select_related("user")
            .first()
        )
        return mapping.user if mapping else None

    @classmethod
    def _find_existing_order(cls, invoice: dict, kv_channel: KiotVietChannel) -> Order | None:
        if bool(kv_channel.old):
            delivery = invoice.get("invoiceDelivery") if isinstance(invoice.get("invoiceDelivery"), dict) else {}
            if not delivery:
                delivery = invoice.get("InvoiceDelivery") if isinstance(invoice.get("InvoiceDelivery"), dict) else {}

            shipping_code = cls._first_non_empty(
                delivery.get("deliveryCode"),
                delivery.get("DeliveryCode"),
            )
            if shipping_code:
                found_by_shipping = Order.objects.filter(
                    shipping_code=shipping_code,
                    deleted_at__isnull=True,
                ).first()
                if found_by_shipping:
                    return found_by_shipping

        invoice_id = cls._to_int(cls._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
        invoice_code = cls._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))

        queryset = Order.objects.filter(kiotviet_channel=kv_channel, deleted_at__isnull=True)
        if invoice_id is not None:
            found = queryset.filter(invoice_id=invoice_id).first()
            if found:
                return found
        if invoice_code:
            found = queryset.filter(invoice_code=invoice_code).first()
            if found:
                return found
        return None

    @classmethod
    def _soft_deleted_order_exists(cls, invoice: dict, kv_channel: KiotVietChannel) -> bool:
        """True nếu hoá đơn này TỪNG có đơn nhưng đã bị user xoá (soft-delete).

        create_only PHẢI coi bản ghi đã xoá là "đã tồn tại": user bulk-delete đơn
        rác/test trong khi hoá đơn KV vẫn sống — không check thì cron lookback + deep
        sweep 01:30 resurrect đơn đã xoá MỖI ĐÊM suốt 31 ngày, xoá đơn thành vô nghĩa
        (audit 2026-07-18)."""
        invoice_id = cls._to_int(cls._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
        invoice_code = cls._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
        queryset = Order.objects.filter(kiotviet_channel=kv_channel, deleted_at__isnull=False)
        if invoice_id is not None and queryset.filter(invoice_id=invoice_id).exists():
            return True
        if invoice_code and queryset.filter(invoice_code=invoice_code).exists():
            return True
        return False

    @classmethod
    def _has_reattached_successor(cls, invoice: dict, kv_channel: KiotVietChannel) -> bool:
        """True nếu mã hoá đơn này đã được reattach sang `<mã>.NN` ở một đơn còn sống.

        Sau reattach, không đơn nào mang mã cũ nữa → `_find_existing_order` trượt và
        import sẽ tạo một dòng đơn (huỷ) trùng. Đơn kế thừa giữ mã cũ trong
        `reattached_invoice_codes` nên nhận diện được (audit 2026-07-14).
        """
        invoice_code = cls._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
        if not invoice_code:
            return False
        return Order.objects.filter(
            kiotviet_channel=kv_channel,
            deleted_at__isnull=True,
            reattached_invoice_codes__contains=[invoice_code],
        ).exists()
