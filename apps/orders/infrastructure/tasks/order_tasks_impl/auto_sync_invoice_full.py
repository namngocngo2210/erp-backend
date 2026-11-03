from datetime import datetime, timedelta, UTC

from apps.orders.contracts.kiotviet_invoice_port import kiotviet_invoice_port
from apps.orders.contracts.kiotviet_order_flow_port import kiotviet_order_flow_port

from .ingest_job_tasks import (
    Decimal,
    Order,
    _publish_auto_order_updated_event,
    logger,
)


# Watermark lần chạy trước (audit 2026-09-23 T1 §2.7): quét `updated_at >= mốc lần
# trước − AUTO_SYNC_INVOICE_FULL_OVERLAP_MINUTES` thay vì 7 ngày cố định mỗi lượt.
# Chưa có mốc (lần đầu / Redis mất) → lùi AUTO_SYNC_INVOICE_FULL_LOOKBACK_DAYS.
_LAST_RUN_CACHE_KEY = "auto_sync_invoice_full:last_run"
_LAST_RUN_CACHE_TTL_SECONDS = 30 * 24 * 3600


def _scan_since(now: datetime) -> tuple[datetime, str]:
    """(mốc bắt đầu quét, nguồn mốc) — `watermark` hay `lookback`."""
    from django.conf import settings
    from django.core.cache import cache

    overlap = timedelta(minutes=int(getattr(settings, "AUTO_SYNC_INVOICE_FULL_OVERLAP_MINUTES", 15)))
    lookback = timedelta(days=int(getattr(settings, "AUTO_SYNC_INVOICE_FULL_LOOKBACK_DAYS", 7)))
    last_run = cache.get(_LAST_RUN_CACHE_KEY)
    if isinstance(last_run, datetime) and last_run.tzinfo is not None:
        # Không để watermark đẩy cửa sổ RỘNG hơn lookback (mốc cũ quá do task
        # ngừng lâu) — cửa sổ lookback là trần chi phí của một lượt chạy.
        return max(last_run - overlap, now - lookback), "watermark"
    return now - lookback, "lookback"


def _remember_run(started_at: datetime) -> None:
    from django.core.cache import cache

    cache.set(_LAST_RUN_CACHE_KEY, started_at, _LAST_RUN_CACHE_TTL_SECONDS)


def process_auto_sync_invoice_full():
    """
    Cron task: Quét các đơn hàng đã có invoice_code cập nhật từ mốc lần chạy trước
    (watermark `updated_at`, fallback 7 ngày) và cập nhật đầy đủ thông tin từ KiotViet API.

    TRẠNG THÁI (audit 2026-09-23 T1 §2.7): KHÔNG còn trong beat
    (config/settings_parts/celery.py) — docs/go-offload-plan.md #6 xếp task này vào
    nhóm port sang Go (`task_id="go"` bên dưới là dấu vết). Giữ hàm Python để chạy
    tay/đối chiếu; xem runbook docs/runbooks/2026-09-23-audit-ops.md mục "Hiệu năng".
    Coi là DEPRECATED khi bản Go đã chạy ổn — xoá cùng `orders/tasks.py` re-export.

    Cập nhật:
    - shipping_code (từ invoiceDelivery.deliveryCode)
    - shipping_fee (từ invoiceDelivery.price)
    - status (từ invoiceDelivery.status)
    - total_amount, total_after_discount (từ Total, totalAmount)
    - discount_amount, booking_voucher_discount, other_price
    - weight, dimensions
    - OrderItem.unit_price (từ invoiceDetails)

    Xử lý theo từng KiotVietChannel để lấy access token tương ứng.
    """
    task_id = "go"
    logger.info(
        "Go task started: process_auto_sync_invoice_full (task_id=%s)",
        task_id,
    )

    # Lấy danh sách kênh KiotViet active (qua port — orders không import model
    # của app kiotviet). Port trả iterator streaming, giữ nguyên chunk_size=100.
    channels = kiotviet_order_flow_port().iter_active_channels()

    total_processed = 0
    total_updated = 0
    total_errors = 0
    channel_results = []

    started_at = datetime.now(UTC)
    scan_since, since_source = _scan_since(started_at)
    logger.info(
        "auto_sync_invoice_full: scan_since=%s (%s)", scan_since.isoformat(), since_source
    )

    for channel in channels:
        channel_id = channel.id
        channel_name = channel.name or f"Channel {channel_id}"

        logger.info(
            "Processing channel (task_id=%s, channel_id=%s, channel_name=%s)",
            task_id,
            channel_id,
            channel_name,
        )

        # Quét các đơn của channel này cập nhật từ mốc lần chạy trước. `items`
        # prefetch theo chunk để `_apply_invoice_detail_price` không tra OrderItem
        # mỗi dòng hoá đơn (trước: 1 query/dòng).
        queryset = (
            Order.objects.select_related("kiotviet_channel", "channel")
            .prefetch_related("items")
            .filter(
                deleted_at__isnull=True,
                kiotviet_channel=channel,
                invoice_code__gt="",
                updated_at__gte=scan_since,
            )
            .order_by("-updated_at")
        )

        total = queryset.count()
        processed = 0
        updated = 0
        errors = 0

        for order in queryset.iterator(chunk_size=100):
            processed += 1

            try:
                invoice_payload = kiotviet_invoice_port().get_invoice_by_code(
                    invoice_code=order.invoice_code, order=order, channel=channel
                )
                if not invoice_payload:
                    logger.warning(
                        "Auto sync invoice full: empty payload (task_id=%s, channel_id=%s, order_id=%s, invoice_code=%s)",
                        task_id,
                        channel_id,
                        order.id,
                        order.invoice_code,
                    )
                    continue

                # Extract invoice delivery info
                invoice_delivery = invoice_payload.get("invoiceDelivery") if isinstance(invoice_payload, dict) else None
                invoice_delivery = invoice_delivery if isinstance(invoice_delivery, dict) else {}

                # Extract invoice details
                invoice_details = invoice_payload.get("invoiceDetails") if isinstance(invoice_payload, dict) else None
                invoice_details = invoice_details if isinstance(invoice_details, list) else []

                changed_fields: list[str] = []
                should_notify_ui = False

                # Update shipping_code
                delivery_code = str(
                    invoice_delivery.get("deliveryCode")
                    or (invoice_payload.get("deliveryCode") if isinstance(invoice_payload, dict) else "")
                    or ""
                ).strip()
                if delivery_code and str(order.shipping_code or "").strip() != delivery_code:
                    order.shipping_code = delivery_code
                    changed_fields.append("shipping_code")

                # Update shipping_fee
                price_raw = invoice_delivery.get("price")
                try:
                    shipping_fee = Decimal(str(price_raw)) if price_raw is not None else Decimal("0")
                except Exception:
                    shipping_fee = Decimal("0")
                if order.shipping_fee != shipping_fee:
                    order.shipping_fee = shipping_fee
                    changed_fields.append("shipping_fee")

                # Update status
                status_raw = invoice_delivery.get("status")
                try:
                    invoice_status = int(status_raw) if status_raw is not None else None
                except (TypeError, ValueError):
                    invoice_status = None

                # invoice_status (from invoice's own status field)
                invoice_own_status_raw = invoice_payload.get("status") if isinstance(invoice_payload, dict) else None
                try:
                    invoice_own_status = int(invoice_own_status_raw) if invoice_own_status_raw is not None else None
                except (TypeError, ValueError):
                    invoice_own_status = None

                # POS sale: invoice hoàn thành (status=1) mà không có invoiceDelivery.status
                # → coi như đã giao (status=3) vì không có vận đơn để theo dõi.
                if invoice_status is None and invoice_own_status == 1:
                    invoice_status = 3

                if invoice_status is not None and order.status != invoice_status:
                    order.status = invoice_status
                    changed_fields.append("status")
                if invoice_own_status is not None and order.invoice_status != invoice_own_status:
                    order.invoice_status = invoice_own_status
                    changed_fields.append("invoice_status")

                # Update total_amount
                total_raw = (invoice_payload.get("Total") or invoice_payload.get("total")) if isinstance(invoice_payload, dict) else None
                try:
                    total_amount = Decimal(str(total_raw)) if total_raw is not None else Decimal("0")
                except Exception:
                    total_amount = Decimal("0")
                if order.total_amount != total_amount:
                    order.total_amount = total_amount
                    changed_fields.append("total_amount")

                # Update total_after_discount
                total_after_discount_raw = (invoice_payload.get("totalAmount") or invoice_payload.get("total_after_discount")) if isinstance(invoice_payload, dict) else None
                try:
                    total_after_discount = Decimal(str(total_after_discount_raw)) if total_after_discount_raw is not None else Decimal("0")
                except Exception:
                    total_after_discount = Decimal("0")
                if order.total_after_discount != total_after_discount:
                    order.total_after_discount = total_after_discount
                    changed_fields.append("total_after_discount")

                # Update discount_amount
                discount_raw = (invoice_payload.get("discountAmount") or invoice_payload.get("discount_amount")) if isinstance(invoice_payload, dict) else None
                try:
                    discount_amount = Decimal(str(discount_raw)) if discount_raw is not None else Decimal("0")
                except Exception:
                    discount_amount = Decimal("0")
                if order.discount_amount != discount_amount:
                    order.discount_amount = discount_amount
                    changed_fields.append("discount_amount")

                # Update booking_voucher_discount
                voucher_raw = (invoice_payload.get("bookingVoucherDiscount") or invoice_payload.get("booking_voucher_discount")) if isinstance(invoice_payload, dict) else None
                try:
                    voucher_discount = Decimal(str(voucher_raw)) if voucher_raw is not None else Decimal("0")
                except Exception:
                    voucher_discount = Decimal("0")
                if order.booking_voucher_discount != voucher_discount:
                    order.booking_voucher_discount = voucher_discount
                    changed_fields.append("booking_voucher_discount")

                # Update other_price
                other_price_raw = (invoice_payload.get("otherPrice") or invoice_payload.get("other_price")) if isinstance(invoice_payload, dict) else None
                try:
                    other_price = Decimal(str(other_price_raw)) if other_price_raw is not None else Decimal("0")
                except Exception:
                    other_price = Decimal("0")
                if order.other_price != other_price:
                    order.other_price = other_price
                    changed_fields.append("other_price")

                # Update weight
                weight_raw = (invoice_delivery.get("weight") or invoice_payload.get("weight")) if isinstance(invoice_payload, dict) else None
                try:
                    weight = Decimal(str(weight_raw)) if weight_raw is not None else Decimal("0")
                except Exception:
                    weight = Decimal("0")
                if order.weight != weight:
                    order.weight = weight
                    changed_fields.append("weight")

                # Update dimensions
                length = (invoice_delivery.get("length") or invoice_payload.get("length")) if isinstance(invoice_payload, dict) else None
                width = (invoice_delivery.get("width") or invoice_payload.get("width")) if isinstance(invoice_payload, dict) else None
                height = (invoice_delivery.get("height") or invoice_payload.get("height")) if isinstance(invoice_payload, dict) else None
                if length and width and height:
                    dimensions = f"{length}x{width}x{height}"
                    if order.dimensions != dimensions:
                        order.dimensions = dimensions
                        changed_fields.append("dimensions")

                # Update payment_method from KiotViet invoice payments[].method.
                payments = invoice_payload.get("payments") if isinstance(invoice_payload, dict) else None
                if not isinstance(payments, list):
                    payments = invoice_payload.get("Payments") if isinstance(invoice_payload, dict) else None
                methods: list[str] = []
                if isinstance(payments, list):
                    for payment in payments:
                        if not isinstance(payment, dict):
                            continue
                        method = str(payment.get("method") or payment.get("Method") or "").strip()
                        if method and method not in methods:
                            methods.append(method)
                if not methods and isinstance(invoice_payload, dict):
                    method = str(invoice_payload.get("method") or invoice_payload.get("Method") or "").strip()
                    if method:
                        methods.append(method)
                payment_method = ", ".join(methods)[:100]
                if payment_method and str(order.payment_method or "").strip() != payment_method:
                    order.payment_method = payment_method
                    changed_fields.append("payment_method")

                # Update OrderItem unit_price / discount theo dòng hoá đơn KV
                for detail in invoice_details:
                    if isinstance(detail, dict) and _apply_invoice_detail_price(order, detail):
                        should_notify_ui = True

                if changed_fields:
                    order.save(update_fields=[*changed_fields, "updated_at"])
                    updated += 1
                    should_notify_ui = True

                if should_notify_ui:
                    _publish_auto_order_updated_event(
                        order=order,
                        source="orders.tasks.process_auto_sync_invoice_full",
                    )

            except Exception as exc:
                errors += 1
                logger.exception(
                    "Auto sync invoice full failed for order_id=%s (task_id=%s, channel_id=%s): %s",
                    order.id,
                    task_id,
                    channel_id,
                    exc,
                )

        total_processed += processed
        total_updated += updated
        total_errors += errors

        channel_results.append(
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "total": total,
                "processed": processed,
                "updated": updated,
                "errors": errors,
            }
        )

        logger.info(
            "Channel processing completed (task_id=%s, channel_id=%s, channel_name=%s, total=%s, processed=%s, updated=%s, errors=%s)",
            task_id,
            channel_id,
            channel_name,
            total,
            processed,
            updated,
            errors,
        )

    _remember_run(started_at)
    logger.info(
        "Go task finished: process_auto_sync_invoice_full (task_id=%s, total_processed=%s, total_updated=%s, total_errors=%s, channels=%s)",
        task_id,
        total_processed,
        total_updated,
        total_errors,
        channel_results,
    )


def _to_decimal_or_zero(value) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _apply_invoice_detail_price(order: Order, detail: dict) -> bool:
    """Đồng bộ đơn giá + giảm giá dòng của `order` theo một dòng hoá đơn KV.

    Đơn giá ưu tiên `priceAfterTax` (invoiceDetailTaxs), không có thì `price`.
    `discount` của KV là giảm cho MỖI sản phẩm — thiếu key thì giữ giảm giá đang
    có, không coi là 0. line_total luôn tính lại = (unit_price − discount) × qty
    để không xoá mất giảm giá dòng khi chỉ đổi giá. Trả True khi có ghi DB.
    """
    from apps.orders.models import OrderItem

    product_code = detail.get("productCode") or detail.get("ProductCode") or detail.get("code") or detail.get("Code")
    if not product_code:
        return False

    unit_price = Decimal("0")
    invoice_detail_taxes = detail.get("invoiceDetailTaxs")
    if isinstance(invoice_detail_taxes, list) and invoice_detail_taxes:
        first_tax = invoice_detail_taxes[0]
        if isinstance(first_tax, dict):
            unit_price = _to_decimal_or_zero(first_tax.get("priceAfterTax", 0))
    if unit_price <= 0:
        unit_price = _to_decimal_or_zero(
            detail.get("price") or detail.get("Price") or detail.get("basePrice") or detail.get("BasePrice")
        )

    # Dùng prefetch cache `items` khi có (đường cron nạp theo chunk) — không tra DB
    # mỗi dòng hoá đơn; instance rời (test/gọi lẻ) rơi về filter như cũ.
    order_item = None
    prefetched = getattr(order, "_prefetched_objects_cache", {}) or {}
    if "items" in prefetched:
        order_item = next((item for item in order.items.all() if item.sku == product_code), None)
    else:
        order_item = OrderItem.objects.filter(order=order, sku=product_code).first()
    if order_item is None:
        return False

    discount_raw = detail.get("discount", detail.get("Discount"))
    discount = _to_decimal_or_zero(discount_raw) if discount_raw is not None else order_item.discount
    if order_item.unit_price == unit_price and order_item.discount == discount:
        return False

    order_item.unit_price = unit_price
    order_item.discount = discount
    order_item.line_total = (unit_price - discount) * order_item.quantity
    order_item.save(update_fields=["unit_price", "discount", "line_total", "updated_at"])
    return True
