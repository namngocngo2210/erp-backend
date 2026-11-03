from __future__ import annotations

from collections import defaultdict
from datetime import date

from apps.customers.services import normalize_phone

from .models import Order, OrderStatus

INVALID_REASON_IN_DELIVERY = "in_delivery"
INVALID_REASON_DUPLICATE_SAME_DAY = "duplicate_same_day"
INVALID_REASON_CONFLICT_CHANNEL = "conflict_channel_same_phone"
INVALID_REASON_BOMB_HISTORY = "bomb_history"
INVALID_REASON_HAS_INVOICE_CODE = "has_invoice_code"

INVALID_REASON_LABELS = {
    INVALID_REASON_IN_DELIVERY: "Đơn đang trong quá trình giao hàng.",
    INVALID_REASON_DUPLICATE_SAME_DAY: "SĐT đã có đơn khác trong cùng ngày.",
    INVALID_REASON_CONFLICT_CHANNEL: "SĐT đã tồn tại ở kênh bán khác.",
    INVALID_REASON_BOMB_HISTORY: "SĐT có lịch sử bom hàng.",
    INVALID_REASON_HAS_INVOICE_CODE: "Đơn đã có mã hóa đơn KiotViet.",
}

_IN_DELIVERY_ASSESSMENT_CODES = {"warehouse_processing", "dispatched", "not_received", "stalled_complaint"}
_BOMB_ASSESSMENT_DELIVERY_CODES = {"bombed"}


def _normalize_order_phone(order: Order) -> str:
    customer = getattr(order, "customer", None)
    customer_phone = str(getattr(customer, "phone_normalized", "") or "").strip()
    if customer_phone:
        return customer_phone

    phone = str(order.phone or "").strip()
    if phone:
        normalized = normalize_phone(phone)
        if normalized:
            return normalized

    customer_raw_phone = str(getattr(customer, "phone_raw", "") or "").strip()
    if not customer_raw_phone:
        return ""
    return normalize_phone(customer_raw_phone)


def _has_bomb_history(order: Order) -> bool:
    assessment = getattr(order, "assessment", None)
    if not assessment:
        return False

    delivery_code = str(getattr(assessment, "delivery_business_status_code", "") or "").strip()
    bomb_group_code = str(getattr(assessment, "bomb_reason_group_code", "") or "").strip()
    bomb_type_code = str(getattr(assessment, "bomb_reason_type_code", "") or "").strip()
    bomb_reason_text = str(getattr(assessment, "bomb_reason_text", "") or "").strip()

    if delivery_code in _BOMB_ASSESSMENT_DELIVERY_CODES:
        return True
    if bomb_group_code or bomb_type_code or bomb_reason_text:
        return True
    return False


def build_kiotviet_push_precheck(order_queryset) -> dict:
    orders = list(order_queryset.select_related("channel", "customer", "assessment"))
    if not orders:
        return {
            "valid_order_ids": [],
            "invalid_orders": [],
            "valid_count": 0,
            "invalid_count": 0,
        }

    normalized_phone_by_order_id: dict[int, str] = {}
    phone_set: set[str] = set()
    for order in orders:
        normalized_phone = _normalize_order_phone(order)
        normalized_phone_by_order_id[order.id] = normalized_phone
        if normalized_phone:
            phone_set.add(normalized_phone)

    duplicate_ids_by_phone_date: dict[tuple[str, date], set[int]] = defaultdict(set)
    channel_ids_by_phone: dict[str, set[int]] = defaultdict(set)
    bomb_history_by_phone: dict[str, bool] = defaultdict(bool)
    if phone_set:
        phone_orders = (
            Order.objects.select_related("channel", "customer", "assessment")
            .filter(deleted_at__isnull=True, customer__phone_normalized__in=phone_set)
        )
        for order in phone_orders:
            normalized_phone = _normalize_order_phone(order)
            if not normalized_phone:
                continue
            duplicate_ids_by_phone_date[(normalized_phone, order.created_at.date())].add(order.id)
            channel_ids_by_phone[normalized_phone].add(order.channel_id)
            if _has_bomb_history(order):
                bomb_history_by_phone[normalized_phone] = True

    invalid_orders: list[dict] = []
    valid_order_ids: list[int] = []
    for order in orders:
        reasons: list[str] = []
        normalized_phone = normalized_phone_by_order_id.get(order.id, "")
        assessment = getattr(order, "assessment", None)

        if str(order.invoice_code or "").strip():
            reasons.append(INVALID_REASON_HAS_INVOICE_CODE)

        delivery_business_status_code = str(
            getattr(assessment, "delivery_business_status_code", "") or ""
        ).strip()
        if order.status == OrderStatus.IN_DELIVERY or delivery_business_status_code in _IN_DELIVERY_ASSESSMENT_CODES:
            reasons.append(INVALID_REASON_IN_DELIVERY)

        if normalized_phone:
            same_day_order_ids = duplicate_ids_by_phone_date.get((normalized_phone, order.created_at.date()), set())
            if any(order_id != order.id for order_id in same_day_order_ids):
                reasons.append(INVALID_REASON_DUPLICATE_SAME_DAY)

            phone_channel_ids = channel_ids_by_phone.get(normalized_phone, set())
            if any(channel_id != order.channel_id for channel_id in phone_channel_ids):
                reasons.append(INVALID_REASON_CONFLICT_CHANNEL)

            if bomb_history_by_phone.get(normalized_phone, False):
                reasons.append(INVALID_REASON_BOMB_HISTORY)

        if reasons:
            invalid_orders.append(
                {
                    "order_id": order.id,
                    "name": order.name,
                    "phone": order.phone or getattr(order.customer, "phone_raw", ""),
                    "channel_code": order.channel.code,
                    "reason_codes": reasons,
                    "reasons": [INVALID_REASON_LABELS[code] for code in reasons],
                }
            )
        else:
            valid_order_ids.append(order.id)

    return {
        "valid_order_ids": valid_order_ids,
        "invalid_orders": invalid_orders,
        "valid_count": len(valid_order_ids),
        "invalid_count": len(invalid_orders),
    }
