"""Special-mode handlers for ImportInvoicesService (mixin moved verbatim from
management/commands/_import_invoices_handlers_mixin.py - WP8.1)."""

import json
import time

from celery.exceptions import SoftTimeLimitExceeded
from django.core.management.base import CommandError
from django.db import transaction

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.infrastructure.services._import_service_upsert_mixin import ImportPageCache
from apps.kiotviet.infrastructure.services.invoice_sync_queries import _extract_invoice_items
from apps.kiotviet.models import KiotVietChannel
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order


class ImportHandlersMixin:
    def _handle_update_status_phone(
        self,
        *,
        kv_channel: KiotVietChannel,
        branch_ids: list[str],
        invoice_codes: list[str],
        dry_run: bool,
        do_status: bool,
        do_phone: bool,
        from_dt: str = "",
        to_dt: str = "",
        target_sale_channel_value: str = "",
    ):
        if do_phone:
            from apps.kiotviet.infrastructure.services.common_auth_customer import find_customer_by_code

        page_size = 100
        current_item = 0
        summary = {"read": 0, "updated": 0, "skipped": 0, "errors": 0, "pages": 0}
        # Đọc theo mã là đọc tươi từng hoá đơn; chỉ đường danh sách mới cần xác minh lại.
        from_list = not invoice_codes

        if invoice_codes:
            from urllib.parse import quote

            all_invoices = []
            summary["pages"] = len(invoice_codes)
            for code in invoice_codes:
                response = _kiotviet_request(
                    order=None,
                    channel=kv_channel,
                    method="GET",
                    endpoint=f"/invoices/code/{quote(code, safe='')}",
                    params={"includePayment": "true", "includeInvoiceDelivery": "true"},
                )
                if response.status_code != 200:
                    summary["errors"] += 1
                    self.stderr.write(
                        self.style.WARNING(
                            f"KiotViet /invoices/code failed code={code}: status={response.status_code}, body={(response.text or '').strip()}"
                        )
                    )
                    continue
                invoice = response.json() if response.content else {}
                if isinstance(invoice, dict) and invoice:
                    all_invoices.append(invoice)
        else:
            all_invoices = []
            while True:
                params = {
                    "pageSize": page_size,
                    "currentItem": current_item,
                    "branchIds": ",".join(branch_ids),
                    "includePayment": "true",
                    "includeInvoiceDelivery": "true",
                    "IncludePayment": "true",
                    "IncludeInvoiceDelivery": "true",
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
                summary["pages"] += 1
                self.stdout.write(f"Update page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}")
                if not invoices:
                    break
                all_invoices.extend(invoices)
                current_item += len(invoices)
                if len(invoices) < page_size:
                    break

        for inv in all_invoices:
            summary["read"] += 1
            inv_code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
            if target_sale_channel_value and self._extract_sale_channel_id(inv) != target_sale_channel_value:
                summary["skipped"] += 1
                continue
            try:
                existing = self._find_existing_order(inv, kv_channel)
                if existing is None:
                    summary["skipped"] += 1
                    continue
                # Cross-channel match: KV cũ (old=True) matched order ở channel khác qua
                # shipping_code. Order đó thuộc về KV mới (A) — không ghi đè từ B.
                if bool(kv_channel.old) and existing.kiotviet_channel_id != kv_channel.id:
                    summary["skipped"] += 1
                    self.stdout.write(
                        f"SKIP cross-channel code={inv_code} order_id={existing.id} "
                        f"order_kv={existing.kiotviet_channel_id} != {kv_channel.id}"
                    )
                    continue

                update_fields: list[str] = []
                previous_status: int | None = None

                if do_status:
                    delivery = inv.get("invoiceDelivery") if isinstance(inv.get("invoiceDelivery"), dict) else {}
                    if not delivery:
                        delivery = inv.get("InvoiceDelivery") if isinstance(inv.get("InvoiceDelivery"), dict) else {}

                    invoice_status_val = self._resolve_invoice_own_status(invoice=inv)
                    # Phải dùng CÙNG resolver với đường import: hoá đơn huỷ (status=2) không
                    # có invoiceDelivery (bán quầy POS) thì map sang CANCELLED. Trước đây chỗ
                    # này chỉ đọc delivery.status rồi mặc định 3 → cron lật đơn huỷ thành "đã
                    # giao"; từ khi cron phát OrderStatusChanged, cú lật đó còn kéo theo trừ
                    # lại tồn của đơn đã huỷ (hàng vẫn nằm trong kho) — audit 2026-07-14 vòng 3.
                    delivery_status_val = self._resolve_invoice_status(invoice=inv, delivery=delivery)

                    # Snapshot DANH SÁCH có thể cũ vài phút (đọc theo trang rồi mới ghi).
                    # Đưa một đơn ĐANG HUỶ về trạng thái sống là thao tác nặng (kho trừ
                    # lại tồn) nên phải hỏi lại KiotViet theo mã trước khi ghi; hỏi không
                    # được thì giữ huỷ, tick sau thử lại (sự cố 14/09/2026, đơn 563397).
                    if from_list and self._snapshot_revives_cancelled(
                        existing, invoice_status_val, delivery_status_val
                    ):
                        fresh = self._fetch_invoice_by_code(kv_channel, inv_code)
                        if fresh is None:
                            summary["skipped"] += 1
                            self.stdout.write(
                                f"SKIP revive {inv_code} order_id={existing.id}: "
                                "không hỏi lại được KiotViet, giữ trạng thái huỷ"
                            )
                            continue
                        inv = fresh
                        delivery = inv.get("invoiceDelivery") if isinstance(inv.get("invoiceDelivery"), dict) else {}
                        if not delivery:
                            delivery = inv.get("InvoiceDelivery") if isinstance(inv.get("InvoiceDelivery"), dict) else {}
                        invoice_status_val = self._resolve_invoice_own_status(invoice=inv)
                        delivery_status_val = self._resolve_invoice_status(invoice=inv, delivery=delivery)
                        if self._snapshot_revives_cancelled(existing, invoice_status_val, delivery_status_val):
                            self.stdout.write(f"REVIVE {inv_code} order_id={existing.id}: KiotViet xác nhận hoá đơn còn sống")
                        else:
                            self.stdout.write(
                                f"SKIP revive {inv_code} order_id={existing.id}: "
                                "snapshot danh sách cũ, KiotViet xác nhận đã huỷ"
                            )

                    # "defaulted" = resolver chịu thua (không delivery, invoice status
                    # không suy được) và ta ép 3 — đừng bật cờ khi resolver TỰ suy ra
                    # được từ invoice status (vd huỷ → 6), log sẽ nói dối "defaulted
                    # to 3" trong khi thực set 6.
                    delivery_status_defaulted = delivery_status_val is None
                    if delivery_status_defaulted:
                        delivery_status_val = 3

                    if invoice_status_val is not None and existing.invoice_status != invoice_status_val:
                        self.stdout.write(
                            f"STATUS {inv_code} order_id={existing.id} "
                            f"invoice_status: {existing.invoice_status} -> {invoice_status_val}"
                        )
                        existing.invoice_status = invoice_status_val
                        update_fields.append("invoice_status")
                    if existing.status != delivery_status_val:
                        self.stdout.write(
                            f"STATUS {inv_code} order_id={existing.id} "
                            f"status: {existing.status} -> {delivery_status_val}"
                            f"{' (delivery.status missing, defaulted to 3)' if delivery_status_defaulted else ''}"
                        )
                        previous_status = int(existing.status)
                        existing.status = delivery_status_val
                        update_fields.append("status")

                if do_phone:
                    customer_code = self._first_non_empty(
                        inv.get("customerCode"),
                        inv.get("CustomerCode"),
                    )
                    if not customer_code:
                        customer_obj = inv.get("customer") if isinstance(inv.get("customer"), dict) else {}
                        customer_code = self._first_non_empty(
                            customer_obj.get("code"),
                            customer_obj.get("Code"),
                        )
                    if customer_code:
                        kv_customer = find_customer_by_code(customer_code, order=None, channel=kv_channel)
                        if kv_customer is not None:
                            phone = self._first_non_empty(
                                kv_customer.get("contactNumber"),
                                kv_customer.get("ContactNumber"),
                                kv_customer.get("phoneNumber"),
                                kv_customer.get("PhoneNumber"),
                            )
                            if phone and existing.phone != phone:
                                existing.phone = phone
                                update_fields.append("phone")
                        else:
                            self.stdout.write(f"Customer not found code={customer_code} for {inv_code}")
                    else:
                        self.stdout.write(f"No customerCode for {inv_code}")

                if not update_fields:
                    summary["skipped"] += 1
                    continue

                if dry_run:
                    self.stdout.write(f"DRY-RUN {inv_code}: {', '.join(update_fields)}")
                else:
                    with transaction.atomic():
                        existing.save(update_fields=[*update_fields, "updated_at"])
                        # Cron 30' này là đường ĐỔI TRẠNG THÁI chạy thường trực ở prod:
                        # phải phát sự kiện như webhook, nếu không kho không biết để hoàn
                        # tồn (đơn lật huỷ) hay trừ lại tồn (đơn huỷ được hồi sinh) —
                        # audit 2026-07-14 vòng 2.
                        if previous_status is not None and previous_status != int(existing.status):
                            event_bus.publish_on_commit(
                                OrderStatusChanged(
                                    order_id=existing.id,
                                    old_status=previous_status,
                                    new_status=int(existing.status),
                                    source=StatusChangeSource.KIOTVIET_SYNC,
                                )
                            )
                    self.stdout.write(f"Updated {inv_code}: {', '.join(update_fields)}")
                summary["updated"] += 1
            except Exception as exc:
                summary["errors"] += 1
                self.stderr.write(self.style.WARNING(f"Update failed code={inv_code}: {exc}"))

        modes = [m for m in ["status" if do_status else "", "phone" if do_phone else ""] if m]
        self.stdout.write(self.style.SUCCESS(f"Update ({', '.join(modes)}) completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    @staticmethod
    def _snapshot_revives_cancelled(existing, invoice_status_val, delivery_status_val) -> bool:
        """Snapshot này có đưa một đơn/hoá đơn ĐANG HUỶ về trạng thái sống không?

        `delivery_status_val is None` cũng tính là hồi sinh: caller sẽ ép 3 ("đang giao").
        """
        from apps.orders.models import OrderStatus

        cancelled = int(OrderStatus.CANCELLED)
        status_revives = int(existing.status or 0) == cancelled and (
            delivery_status_val is None or int(delivery_status_val) != cancelled
        )
        invoice_revives = (
            existing.invoice_status == 2
            and invoice_status_val is not None
            and int(invoice_status_val) != 2
        )
        return status_revives or invoice_revives

    def _fetch_invoice_by_code(self, kv_channel: KiotVietChannel, code: str) -> dict | None:
        """Hỏi lại KiotViet một hoá đơn theo mã. None = không lấy được (lỗi HTTP/payload)."""
        from urllib.parse import quote

        try:
            response = _kiotviet_request(
                order=None,
                channel=kv_channel,
                method="GET",
                endpoint=f"/invoices/code/{quote(str(code), safe='')}",
                params={"includePayment": "true", "includeInvoiceDelivery": "true"},
            )
        except Exception as exc:  # noqa: BLE001 — lỗi mạng: coi như không xác minh được
            self.stderr.write(self.style.WARNING(f"KiotViet /invoices/code failed code={code}: {exc}"))
            return None
        if response.status_code != 200:
            self.stderr.write(
                self.style.WARNING(
                    f"KiotViet /invoices/code failed code={code}: status={response.status_code}, "
                    f"body={(getattr(response, 'text', '') or '').strip()}"
                )
            )
            return None
        invoice = response.json() if response.content else {}
        return invoice if isinstance(invoice, dict) and invoice else None

    def _handle_resync_codes(
        self,
        *,
        kv_channel: KiotVietChannel,
        branch_ids: list[str],
        invoice_codes: list[str],
        dry_run: bool,
        from_dt: str = "",
        to_dt: str = "",
        target_sale_channel_value: str = "",
    ):
        page_size = 100
        current_item = 0
        summary = {"read": 0, "updated": 0, "skipped": 0, "errors": 0, "pages": 0}

        if invoice_codes:
            from urllib.parse import quote

            all_invoices: list[dict] = []
            summary["pages"] = len(invoice_codes)
            for code in invoice_codes:
                response = _kiotviet_request(
                    order=None,
                    channel=kv_channel,
                    method="GET",
                    endpoint=f"/invoices/code/{quote(code, safe='')}",
                    params={"includePayment": "true", "includeInvoiceDelivery": "true"},
                )
                if response.status_code != 200:
                    summary["errors"] += 1
                    self.stderr.write(
                        self.style.WARNING(
                            f"KiotViet /invoices/code failed code={code}: status={response.status_code}, body={(response.text or '').strip()}"
                        )
                    )
                    continue
                invoice = response.json() if response.content else {}
                if isinstance(invoice, dict) and invoice:
                    all_invoices.append(invoice)
        else:
            all_invoices = []
            while True:
                params = {
                    "pageSize": page_size,
                    "currentItem": current_item,
                    "branchIds": ",".join(branch_ids),
                    "includePayment": "true",
                    "includeInvoiceDelivery": "true",
                    "IncludePayment": "true",
                    "IncludeInvoiceDelivery": "true",
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
                summary["pages"] += 1
                self.stdout.write(
                    f"Resync page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
                )
                if not invoices:
                    break
                all_invoices.extend(invoices)
                current_item += len(invoices)
                if len(invoices) < page_size:
                    break

        for inv in all_invoices:
            summary["read"] += 1
            order_code = self._first_non_empty(inv.get("orderCode"), inv.get("OrderCode"))
            if target_sale_channel_value and self._extract_sale_channel_id(inv) != target_sale_channel_value:
                summary["skipped"] += 1
                continue
            try:
                if not order_code:
                    summary["skipped"] += 1
                    continue
                existing = Order.objects.filter(
                    kiotviet_channel=kv_channel,
                    order_code=order_code,
                    deleted_at__isnull=True,
                ).first()
                if existing is None:
                    summary["skipped"] += 1
                    continue

                delivery = inv.get("invoiceDelivery") if isinstance(inv.get("invoiceDelivery"), dict) else {}
                if not delivery:
                    delivery = inv.get("InvoiceDelivery") if isinstance(inv.get("InvoiceDelivery"), dict) else {}

                invoice_code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
                invoice_id = self._to_int(self._first_non_empty(inv.get("id"), inv.get("invoiceId")))
                invoice_status_val = self._resolve_invoice_own_status(invoice=inv)
                delivery_status_val = self._resolve_delivery_status(delivery=delivery)
                previous_status: int | None = None

                update_fields: list[str] = []
                had_invoice_code = bool(str(existing.invoice_code or "").strip())
                if invoice_code and existing.invoice_code != invoice_code:
                    existing.invoice_code = invoice_code
                    update_fields.append("invoice_code")
                    if not had_invoice_code and not dry_run:
                        # Mốc "đã tạo hóa đơn" — chỉ lần gán invoice_code ĐẦU TIÊN
                        # (spec 2026-08-04-order-milestone-snapshots).
                        from apps.orders.application.services.milestone_snapshot_service import (
                            capture_invoice_snapshot,
                        )

                        capture_invoice_snapshot(existing)
                if invoice_id is not None and existing.invoice_id != invoice_id:
                    existing.invoice_id = invoice_id
                    update_fields.append("invoice_id")
                if invoice_status_val is not None and existing.invoice_status != invoice_status_val:
                    existing.invoice_status = invoice_status_val
                    update_fields.append("invoice_status")
                if delivery_status_val is not None and existing.status != delivery_status_val:
                    previous_status = int(existing.status)
                    existing.status = delivery_status_val
                    update_fields.append("status")

                if not update_fields:
                    summary["skipped"] += 1
                    continue

                if dry_run:
                    self.stdout.write(f"DRY-RUN order_code={order_code}: {', '.join(update_fields)}")
                else:
                    with transaction.atomic():
                        # Giải phóng invoice_code/invoice_id đang bị order khác giữ
                        # (cùng channel) để ghi đè cho khớp KiotViet.
                        conflict_q = Order.objects.filter(
                            kiotviet_channel=kv_channel,
                            deleted_at__isnull=True,
                        ).exclude(id=existing.id)
                        conflict_filters: list[dict] = []
                        if "invoice_code" in update_fields and invoice_code:
                            conflict_filters.append({"invoice_code": invoice_code})
                        if "invoice_id" in update_fields and invoice_id is not None:
                            conflict_filters.append({"invoice_id": invoice_id})
                        seen_ids: set[int] = set()
                        for cf in conflict_filters:
                            for conflict in conflict_q.filter(**cf):
                                if conflict.id in seen_ids:
                                    continue
                                seen_ids.add(conflict.id)
                                self.stdout.write(
                                    f"CONFLICT cleared invoice fields on order_code={conflict.order_code} "
                                    f"(id={conflict.id}, invoice_code={conflict.invoice_code}, invoice_id={conflict.invoice_id}) "
                                    f"for resync of order_code={order_code}"
                                )
                                conflict.invoice_code = ""
                                conflict.invoice_id = None
                                conflict.save(update_fields=["invoice_code", "invoice_id", "updated_at"])
                        existing.save(update_fields=[*update_fields, "updated_at"])
                        # Bất biến: mọi đường ĐỔI Order.status đều phải phát sự kiện, nếu
                        # không kho im lặng lệch tồn (audit 2026-07-14 vòng 3).
                        if previous_status is not None and previous_status != int(existing.status):
                            event_bus.publish_on_commit(
                                OrderStatusChanged(
                                    order_id=existing.id,
                                    old_status=previous_status,
                                    new_status=int(existing.status),
                                    source=StatusChangeSource.KIOTVIET_SYNC,
                                )
                            )
                    self.stdout.write(f"Resync order_code={order_code}: {', '.join(update_fields)}")
                summary["updated"] += 1
            except Exception as exc:
                summary["errors"] += 1
                self.stderr.write(self.style.WARNING(f"Resync failed order_code={order_code}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Resync codes completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    def _handle_check_channel_match(
        self,
        *,
        kv_channel: KiotVietChannel,
        branch_ids: list[str],
        invoice_codes: list[str],
        dry_run: bool,
        from_dt: str = "",
        to_dt: str = "",
        target_sale_channel_value: str = "",
        only_current_channel_ids: list[int] | None = None,
        empty_sale_channel_to: Channel | None = None,
    ):
        page_size = 100
        current_item = 0
        only_current = set(only_current_channel_ids or [])
        summary = {
            "read": 0,
            "matched": 0,
            "fixed": 0,
            "missing_order": 0,
            # saleChannelId rỗng trên KV (đơn bán tại quầy / không gắn kênh)
            "empty_sale_channel": 0,
            # saleChannelId có giá trị nhưng chưa có ChannelKiotvietMapping —
            # KHÔNG được dồn về kênh fallback, đây là doanh thu của shop thật
            # chưa được khai báo kênh (audit 2026-08-23).
            "unmapped_sale_channel": 0,
            "out_of_scope": 0,
            "errors": 0,
            "pages": 0,
        }
        unmapped_counts: dict[str, int] = {}

        if invoice_codes:
            from urllib.parse import quote

            all_invoices: list[dict] = []
            summary["pages"] = len(invoice_codes)
            for code in invoice_codes:
                response = _kiotviet_request(
                    order=None,
                    channel=kv_channel,
                    method="GET",
                    endpoint=f"/invoices/code/{quote(code, safe='')}",
                    params={"includePayment": "true", "includeInvoiceDelivery": "true"},
                )
                if response.status_code != 200:
                    summary["errors"] += 1
                    self.stderr.write(
                        self.style.WARNING(
                            f"KiotViet /invoices/code failed code={code}: status={response.status_code}, body={(response.text or '').strip()}"
                        )
                    )
                    continue
                invoice = response.json() if response.content else {}
                if isinstance(invoice, dict) and invoice:
                    all_invoices.append(invoice)
        else:
            all_invoices = []
            while True:
                params = {
                    "pageSize": page_size,
                    "currentItem": current_item,
                    "branchIds": ",".join(branch_ids),
                    "includePayment": "true",
                    "includeInvoiceDelivery": "true",
                    "IncludePayment": "true",
                    "IncludeInvoiceDelivery": "true",
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
                summary["pages"] += 1
                self.stdout.write(
                    f"Check page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
                )
                if not invoices:
                    break
                all_invoices.extend(invoices)
                current_item += len(invoices)
                if len(invoices) < page_size:
                    break

        for inv in all_invoices:
            inv_code = self._first_non_empty(inv.get("code"), inv.get("invoiceCode"))
            try:
                summary["read"] += 1

                sale_channel_id = self._extract_sale_channel_id(inv)
                if target_sale_channel_value and sale_channel_id != target_sale_channel_value:
                    continue
                expected_channel = self._resolve_order_channel_from_sale_channel_id(
                    kv_channel=kv_channel,
                    sale_channel_id=sale_channel_id,
                )
                if expected_channel is None:
                    if str(sale_channel_id or "").strip():
                        # Có saleChannelId nhưng thiếu mapping → chỉ THỐNG KÊ, không
                        # gán fallback: dồn về "Khác" sẽ chôn doanh thu của shop thật.
                        summary["unmapped_sale_channel"] += 1
                        key = str(sale_channel_id).strip()
                        unmapped_counts[key] = unmapped_counts.get(key, 0) + 1
                        self.stdout.write(
                            f"UNMAPPED_SALE_CHANNEL code={inv_code} sale_channel_id={key}"
                        )
                        continue
                    summary["empty_sale_channel"] += 1
                    if empty_sale_channel_to is None:
                        self.stdout.write(f"EMPTY_SALE_CHANNEL code={inv_code}")
                        continue
                    expected_channel = empty_sale_channel_to

                existing = self._find_existing_order(inv, kv_channel)
                if existing is None:
                    summary["missing_order"] += 1
                    self.stdout.write(f"MISSING_ORDER code={inv_code}")
                    continue
                # Cross-channel match: KV cũ (old=True) matched order ở channel khác qua
                # shipping_code. Order đó thuộc về KV mới (A) — không ghi đè từ B.
                if bool(kv_channel.old) and existing.kiotviet_channel_id != kv_channel.id:
                    self.stdout.write(
                        f"SKIP cross-channel code={inv_code} order_id={existing.id} "
                        f"order_kv={existing.kiotviet_channel_id} != {kv_channel.id}"
                    )
                    continue

                current_channel_id = existing.channel_id
                if current_channel_id == expected_channel.id:
                    summary["matched"] += 1
                    continue

                # Chỉ đụng vào đơn đang nằm ở các kênh được chỉ định (vd 3 kênh
                # fallback kiotviet-*). Không có filter = sửa mọi đơn lệch, kể cả
                # đơn đã được người dùng sửa tay → dễ ghi đè oan.
                if only_current and current_channel_id not in only_current:
                    summary["out_of_scope"] += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN MISMATCH code={inv_code} order_id={existing.id} "
                        f"channel_id: {current_channel_id} -> {expected_channel.id} "
                        f"({expected_channel.code})"
                    )
                else:
                    existing.channel = expected_channel
                    existing.save(update_fields=["channel", "updated_at"])
                    self.stdout.write(
                        f"FIXED code={inv_code} order_id={existing.id} "
                        f"channel_id: {current_channel_id} -> {expected_channel.id} "
                        f"({expected_channel.code})"
                    )
                summary["fixed"] += 1
            except Exception as exc:
                summary["errors"] += 1
                self.stderr.write(self.style.WARNING(f"check failed code={inv_code}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Channel match check completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
        if unmapped_counts:
            ranked = sorted(unmapped_counts.items(), key=lambda item: item[1], reverse=True)
            self.stdout.write(
                self.style.WARNING(
                    f"Sale channel chưa có mapping (kv_id={kv_channel.id}): {len(ranked)} id, "
                    f"{sum(unmapped_counts.values())} đơn. Khai báo Channel + mapping rồi chạy lại."
                )
            )
            self.stdout.write(json.dumps([
                {"sale_channel_id": key, "invoices": count} for key, count in ranked
            ], ensure_ascii=False))

    def _handle_import(
        self,
        *,
        kv_channel: KiotVietChannel,
        branch_ids: list[str],
        dry_run: bool,
        create_only: bool,
        list_missing: bool,
        from_dt: str,
        to_dt: str,
        target_sale_channel_value: str,
    ) -> dict:
        """Phân trang /invoices và upsert (hoặc liệt kê missing). Trả về summary dict.

        Tách từ Command.handle() để dùng chung cho CLI và cron import create-only.
        """
        summary = {
            "channel_id": kv_channel.id,
            "branch_ids": branch_ids,
            "dry_run": dry_run,
            "read": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "pages": 0,
        }
        missing_invoices: list[dict] = []
        page_size = 100
        current_item = 0

        while True:
            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "branchIds": ",".join(branch_ids),
                "includePayment": "true",
                "includeInvoiceDelivery": "true",
                # Giữ thêm biến thể PascalCase để tương thích endpoint trả dữ liệu cũ/mới.
                "IncludePayment": "true",
                "IncludeInvoiceDelivery": "true",
                # Sort theo id (bất biến) để phân trang currentItem ỔN ĐỊNH. Không có sort
                # này, public /invoices mặc định theo modifiedDate — đơn bị sửa trong lúc
                # quét (carrier cập nhật trạng thái giao) làm hàng xê dịch giữa trang →
                # vừa lặp vừa bỏ sót; cron create-only vì vậy sót cả đơn trong ngày
                # (cùng bài học với pre-scan reconcile: ~449/15485 đơn).
                "orderBy": "id",
                "orderDirection": "Asc",
            }
            self._apply_date_params(params, from_dt, to_dt)
            # Retry per-page với backoff: deep sweep 31 ngày ≈ 155 request liên tục —
            # profile dễ dính KV 429/5xx ban đêm; 1 response non-200 mà hủy cả run là
            # mất nguyên đêm quét (purge mixin đã có retry, import loop từng không —
            # audit 2026-07-18).
            response = None
            for attempt, delay in enumerate((0, 2, 5, 10)):
                if delay:
                    time.sleep(delay)
                response = _kiotviet_request(
                    order=None,
                    channel=kv_channel,
                    method="GET",
                    endpoint="/invoices",
                    params=params,
                )
                if response.status_code == 200:
                    break
                self.stderr.write(
                    self.style.WARNING(
                        f"KiotViet /invoices non-200 (attempt {attempt + 1}): "
                        f"status={response.status_code}"
                    )
                )
            body = (response.text or "").strip()
            if response.status_code != 200:
                raise CommandError(
                    f"KiotViet /invoices failed: status={response.status_code}, body={body}"
                )

            payload = response.json() if response.content else {}
            invoices = _extract_invoice_items(payload)

            summary["pages"] += 1
            self.stdout.write(
                f"Page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
            )

            if not invoices:
                break

            # Nạp trước Product/Customer cho cả trang: 3 query thay vì 4-8 query mỗi
            # hoá đơn (audit 2026-09-23 T1 §2.4). dry_run/list_missing không ghi gì
            # nên không cần.
            page_cache = (
                None if dry_run or list_missing else ImportPageCache.build(invoices, type(self))
            )

            for invoice in invoices:
                summary["read"] += 1
                if target_sale_channel_value and self._extract_sale_channel_id(invoice) != target_sale_channel_value:
                    summary["skipped"] += 1
                    continue
                if list_missing:
                    try:
                        existing = self._find_existing_order(invoice, kv_channel)
                    except Exception as exc:
                        summary["errors"] += 1
                        self.stderr.write(self.style.WARNING(f"Lookup failed: {exc}"))
                        continue
                    if existing is None:
                        invoice_code = self._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
                        invoice_id = self._to_int(self._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
                        missing_invoices.append({"id": invoice_id, "code": invoice_code})
                        summary["created"] += 1
                        self.stdout.write(f"MISSING id={invoice_id} code={invoice_code}")
                    else:
                        summary["skipped"] += 1
                    continue
                try:
                    result = self._upsert_invoice(
                        invoice=invoice,
                        kv_channel=kv_channel,
                        dry_run=dry_run,
                        create_only=create_only,
                        page_cache=page_cache,
                    )
                    summary[result] += 1
                except SoftTimeLimitExceeded:
                    # KHÔNG nuốt chung với lỗi 1 invoice: nuốt là chạy tiếp tới
                    # hard-kill — bài học đã vá ở update_invoice_status_targets
                    # (audit 14e), nay vá nốt ở import loop (audit 2026-07-18).
                    raise
                except Exception as exc:
                    summary["errors"] += 1
                    invoice_code = self._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
                    invoice_id = self._to_int(self._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
                    self.stderr.write(
                        self.style.WARNING(
                            f"Invoice failed id={invoice_id} code={invoice_code}: {exc}"
                        )
                    )

            current_item += len(invoices)
            if len(invoices) < page_size:
                break

        self.stdout.write(self.style.SUCCESS("Import KiotViet invoices completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
        if list_missing:
            self.stdout.write(self.style.SUCCESS(f"Missing total: {len(missing_invoices)}"))
            self.stdout.write(json.dumps(missing_invoices, ensure_ascii=False))
        return summary

