"""Dữ liệu đơn hàng + hành trình cho portal khách hàng (đã lọc bỏ dữ liệu nội bộ)."""

from django.db.models import Q

from apps.warranty_codes.domain.order_labels import status_label


def order_summary(order) -> dict:
    """Tóm tắt đơn cho tab Vận đơn.

    Vẫn KHÔNG trả `id`, `total_amount`, địa chỉ đầy đủ hay dữ liệu vận hành nội
    bộ (sale phụ trách, ghi chú, kho...). Từ bản sửa đổi 10/08 có thêm tên khách,
    SĐT và mã hoá đơn: muốn tới được đây người gọi đã phải nhập ĐÚNG cặp mã vận
    đơn + SĐT của đơn (xem `portal_views.track`), nên ba trường này không lộ gì
    ngoài những thứ chính chủ đơn đã biết — đổi lại khách xác nhận được "đúng đơn
    của mình" trước khi đọc hành trình.
    """
    return {
        "order_code": order.order_code or "",
        "invoice_code": order.invoice_code or "",
        "shipping_code": order.shipping_code or "",
        "created_at": order.created_at,
        "status": order.status,
        "status_label": status_label(order.status),
        "customer_name": order.name or "",
        "phone": order.phone or "",
        "province": order.province or "",
        "items": [
            {"sku": item.sku, "name": item.product_name, "quantity": item.quantity}
            for item in order.items.all()
        ],
    }


def order_journey(order) -> dict:
    from apps.best_express.models import BestShipment, BestWebhookEvent
    from apps.orders.models import OrderJourneyEvent
    from apps.warranty.tracking_status import BEST_VC_GROUP_LABELS, map_best_vc_group

    shipment = BestShipment.objects.filter(order=order).order_by("-created_at").first()
    if shipment is not None:
        codes = [c for c in (shipment.code_best, shipment.merchant_order_code) if c]
        # NOTE: `.union()` không cho order_by trên field tuỳ ý ở mọi backend (và ở đây
        # còn union hai lát cắt của CÙNG một bảng) — dùng OR qua Q() cho đơn giản và
        # chắc chắn portable hơn union().order_by().
        events = (
            BestWebhookEvent.objects.filter(
                Q(code_best__in=codes) | Q(merchant_order_code__in=codes)
            ).order_by("received_at")
            if codes
            else []
        )
        results = []
        for event in events:
            group = map_best_vc_group(event.best_status_code)
            if group is None:
                continue
            results.append(
                {"at": event.received_at, "label": BEST_VC_GROUP_LABELS[group], "group": group}
            )
        if results:
            return {"results": results, "source": "best"}

    internal = (
        OrderJourneyEvent.objects.filter(order=order, event_type="order_status_changed")
        .order_by("created_at")
        .values("created_at", "to_code")
    )
    results = [
        {
            "at": row["created_at"],
            "label": status_label(int(row["to_code"])),
            "group": "status",
        }
        for row in internal
        if (row["to_code"] or "").isdigit()
    ]
    return {"results": results, "source": "internal"}
