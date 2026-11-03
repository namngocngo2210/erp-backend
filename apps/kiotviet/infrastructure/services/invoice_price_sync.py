"""Đồng bộ tiền từ hóa đơn KiotViet về đơn CRM (lệnh ``sync_invoice_prices``).

Gộp hai lệnh cũ ``sync_invoice_amounts`` (tổng tiền đơn) và
``sync_invoice_item_prices`` (đơn giá từng dòng) — audit 2026-09-23 T3/P2-5: khung
duyệt đơn (kênh KiotViet → đơn có ``invoice_code`` → gọi ``get_invoice_by_code``,
dry-run, log, summary) y hệt nhau, chỉ khác bước áp số liệu. Mỗi target giữ đúng
hành vi và thông điệp của bản cũ; ``both`` gọi KiotViet MỘT lần cho mỗi đơn rồi
áp cả hai.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem

TARGET_AMOUNTS = "amounts"
TARGET_ITEMS = "items"
TARGET_BOTH = "both"
TARGET_CHOICES = (TARGET_AMOUNTS, TARGET_ITEMS, TARGET_BOTH)

Writer = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def first_non_empty(*values) -> str:
    for value in values:
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return ""


def invoice_orders(channel: KiotVietChannel, *, limit: int = 0) -> tuple[Iterable[Order], int]:
    """Đơn còn sống có ``invoice_code`` của kênh, theo id tăng dần; ``limit`` 0 = tất cả."""
    queryset = Order.objects.filter(
        kiotviet_channel=channel,
        invoice_code__gt="",
        deleted_at__isnull=True,
    ).order_by("id")
    if limit > 0:
        orders = list(queryset[:limit])
        return orders, len(orders)
    return queryset, queryset.count()


def invoice_price_map(invoice: dict) -> dict[str, Decimal] | None:
    """``productCode → priceAfterTax`` (fallback ``price``) từ chi tiết hóa đơn.

    ``None`` = hóa đơn không có dòng chi tiết (khác dict rỗng = có dòng nhưng
    không dòng nào có giá hợp lệ) — hai thông điệp cảnh báo khác nhau.
    """
    details = invoice.get("invoiceDetails")
    if not isinstance(details, list):
        details = invoice.get("InvoiceDetails")
    if not details:
        return None

    price_map: dict[str, Decimal] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        product_code = first_non_empty(
            detail.get("productCode"),
            detail.get("ProductCode"),
            detail.get("code"),
            detail.get("Code"),
        )
        if not product_code:
            continue
        price = Decimal("0")
        taxes = detail.get("invoiceDetailTaxs")
        if isinstance(taxes, list) and taxes and isinstance(taxes[0], dict):
            price = to_decimal(taxes[0].get("priceAfterTax", 0))
        if price <= 0:
            price = to_decimal(first_non_empty(detail.get("price"), detail.get("Price"), 0))
        if price > 0:
            price_map[product_code] = price
    return price_map


def invoice_total(invoice: dict) -> Decimal:
    """``Total`` của hóa đơn, thử thêm ``total`` / ``totalAmount`` khi thiếu."""
    total = to_decimal(invoice.get("Total"), Decimal("0"))
    if total <= 0:
        total = to_decimal(invoice.get("total"), invoice.get("totalAmount", 0))
    return total


class InvoicePriceSyncer:
    """Duyệt đơn của một kênh, gọi KiotViet một lần mỗi đơn, áp target đã chọn."""

    def __init__(
        self,
        channel: KiotVietChannel,
        *,
        targets: Iterable[str],
        dry_run: bool = False,
        limit: int = 0,
        out: Writer = _noop,
        warn: Writer = _noop,
        err: Writer = _noop,
    ):
        self.channel = channel
        self.targets = set(targets)
        if TARGET_BOTH in self.targets:
            self.targets = {TARGET_AMOUNTS, TARGET_ITEMS}
        unknown = self.targets - {TARGET_AMOUNTS, TARGET_ITEMS}
        if unknown:
            raise ValueError(f"target không hợp lệ: {sorted(unknown)}")
        self.dry_run = dry_run
        self.limit = limit
        self.out, self.warn, self.err = out, warn, err

    def run(self) -> dict:
        orders, total_orders = invoice_orders(self.channel, limit=self.limit)
        self.out(
            f"Found {total_orders} orders with invoice_code for channel {self.channel.id}"
        )
        summary: dict = {
            "channel_id": self.channel.id,
            "dry_run": self.dry_run,
            "targets": sorted(self.targets),
            "total_orders": total_orders,
            "processed": 0,
            "errors": 0,
        }
        if TARGET_AMOUNTS in self.targets:
            summary.update({"amounts_updated": 0, "amounts_skipped": 0})
        if TARGET_ITEMS in self.targets:
            summary.update({"items_updated": 0, "items_skipped": 0})

        for order in orders:
            summary["processed"] += 1
            try:
                invoice = get_invoice_by_code(
                    invoice_code=order.invoice_code, order=order, channel=self.channel,
                )
                if TARGET_AMOUNTS in self.targets:
                    self._sync_amounts(order, invoice, summary)
                if TARGET_ITEMS in self.targets:
                    self._sync_items(order, invoice, summary)
            except Exception as exc:  # một đơn hỏng không được chặn cả lô (như lệnh cũ)
                summary["errors"] += 1
                self.err(f"Order {order.id} (invoice_code={order.invoice_code}): {exc}")
        return summary

    def _sync_amounts(self, order: Order, invoice: dict, summary: dict) -> None:
        new_total = invoice_total(invoice)
        if new_total <= 0:
            self.warn(
                f"Order {order.id}: invoice {order.invoice_code} has total = 0 or not found, skipping"
            )
            summary["amounts_skipped"] += 1
            return
        current_total = order.total_amount or Decimal("0")
        current_after_discount = order.total_after_discount or Decimal("0")
        if current_total == new_total and current_after_discount == new_total:
            summary["amounts_skipped"] += 1
            return
        message = (
            f"Order {order.id}: total_amount {current_total} -> {new_total}, "
            f"total_after_discount {current_after_discount} -> {new_total}"
        )
        if self.dry_run:
            self.out(f"[DRY-RUN] {message}")
        else:
            with transaction.atomic():
                order.total_amount = new_total
                order.total_after_discount = new_total
                order.save(update_fields=["total_amount", "total_after_discount", "updated_at"])
            self.out(message)
        summary["amounts_updated"] += 1

    def _sync_items(self, order: Order, invoice: dict, summary: dict) -> None:
        price_map = invoice_price_map(invoice)
        if price_map is None:
            self.warn(f"Order {order.id}: invoice {order.invoice_code} has no details, skipping")
            summary["items_skipped"] += 1
            return
        if not price_map:
            self.warn(f"Order {order.id}: no valid prices found in invoice details")
            summary["items_skipped"] += 1
            return
        order_items = OrderItem.objects.filter(order=order)
        if not order_items.exists():
            self.warn(f"Order {order.id}: no order items found")
            summary["items_skipped"] += 1
            return

        items_to_update = []
        for item in order_items:
            invoice_price = price_map.get(item.sku)
            if invoice_price is None:
                summary["items_skipped"] += 1
                continue
            # Giữ giảm giá dòng đang có.
            new_line_total = (invoice_price - (item.discount or 0)) * item.quantity
            if item.unit_price == invoice_price and item.line_total == new_line_total:
                summary["items_skipped"] += 1
                continue
            if self.dry_run:
                self.out(
                    f"[DRY-RUN] Order {order.id} item {item.id} (sku={item.sku}): "
                    f"unit_price {item.unit_price} -> {invoice_price}, "
                    f"line_total {item.line_total} -> {new_line_total}"
                )
            else:
                item.unit_price = invoice_price
                item.line_total = new_line_total
                items_to_update.append(item)
            summary["items_updated"] += 1

        if items_to_update and not self.dry_run:
            with transaction.atomic():
                for item in items_to_update:
                    item.save(update_fields=["unit_price", "line_total", "updated_at"])
