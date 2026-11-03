"""Mapping thuần: model nội bộ → payload CNV Loyalty (kiểu Shopify).

Pure functions, không I/O, không query — nhận sẵn dữ liệu đã đọc, trả dict.
Tiền tệ: CNV nhận số nguyên VND (ví dụ trong tài liệu: ``"price": 35000``) —
Decimal nội bộ được ép ``int``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from apps.orders.models import OrderStatus

# unique_id gửi CNV để CNV tự chống trùng đơn khi mình retry (tài liệu CNV:
# "Optional: your unique key of order").
UNIQUE_ID_PREFIX = "desi"


def split_vietnamese_name(full_name: str) -> tuple[str, str]:
    """Tách họ tên VN → ``(last_name, first_name)`` theo quy ước CNV.

    Ví dụ trong tài liệu CNV: ``first_name="HỒ VĂN KHOA"`` để nguyên cả cụm khi
    không tách được, nhưng chuẩn chung của họ là từ CUỐI = tên gọi. Tên rỗng trả
    placeholder "Khách" — CNV yêu cầu first_name khi tạo customer.
    """
    parts = (full_name or "").split()
    if not parts:
        return "", "Khách"
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _money(value: Decimal | int | float | None) -> int:
    if value is None:
        return 0
    return int(value)


def map_customer(customer: Any) -> dict:
    last_name, first_name = split_vietnamese_name(customer.full_name)
    return {
        "customer": {
            "last_name": last_name,
            "first_name": first_name,
            "phone": customer.phone_normalized or customer.phone_raw or "",
        }
    }


def map_product(product: Any) -> dict:
    """Product nội bộ (1 SKU) → product CNV 1 variant mặc định.

    Payload bám sát mẫu curl trong tài liệu CNV (options + đủ field variant):
    bản tối giản ban đầu bị CNV trả "Data is invalid" trên prod 2026-08-06.
    """
    return {
        "product": {
            "title": product.name,
            "body_html": "",
            "handle": "",
            "product_type": "Khác",
            "vendor": "Khác",
            "published_at": product.created_at.isoformat(),
            "tags": "",
            "options": [
                {"id": 0, "name": "Title", "position": 1, "product_id": 0},
            ],
            "variants": [
                {
                    "id": 0,
                    "product_id": 0,
                    "image_id": 0,
                    "title": "Default Title",
                    "option1": "Default Title",
                    "option2": "",
                    "option3": "",
                    "sku": product.sku,
                    "barcode": "",
                    "price": _money(product.price),
                    "compare_at_price": 0,
                    "cost_per_item": 0,
                    "position": 1,
                    "inventory_management": False,
                    "inventory_policy": False,
                    "inventory_quantity": 0,
                    "old_inventory_quantity": 0,
                    "requires_shipping": True,
                    "weight": _money(product.weight),
                    "weight_unit": "kg",
                    "grams": 0,
                }
            ],
            "images": [],
            "metafields": [],
        }
    }


def map_custom_collection(category: Any) -> dict:
    return {
        "custom_collection": {
            "title": category.name,
            "handle": category.code,
            "published": bool(category.is_active),
        }
    }


def map_order_status(status: int) -> dict:
    """OrderStatus nội bộ → bộ 3 trạng thái CNV (bảng ở spec §3.5).

    Chỉ 3 mốc chốt nghiệp vụ đổi trạng thái phía CNV: giao thành công (tích
    điểm), trả hàng (hoàn điểm), hủy. Mọi trạng thái trung gian là ``pending``.
    """
    if status == OrderStatus.DELIVERED_SUCCESS:
        return {
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "cancelled_status": "uncancelled",
            "confirmed": True,
        }
    if status == OrderStatus.RETURNED:
        return {
            "financial_status": "refunded",
            "fulfillment_status": "restocked",
            "cancelled_status": "uncancelled",
            "confirmed": True,
        }
    if status == OrderStatus.CANCELLED:
        return {
            "financial_status": "pending",
            "fulfillment_status": None,
            "cancelled_status": "cancelled",
            "confirmed": False,
        }
    return {
        "financial_status": "pending",
        "fulfillment_status": None,
        "cancelled_status": "uncancelled",
        "confirmed": False,
    }


def build_unique_id(order_id: int) -> str:
    return f"{UNIQUE_ID_PREFIX}-{order_id}"


def map_order_customer(order: Any, customer_cnv_id: int | None) -> dict:
    """Khối ``order.customer`` — luôn mang SĐT + tên, kèm ``id`` nếu đã có mapping.

    Khách đã tồn tại sẵn phía CNV thì mình không có id (CNV không cho tra theo
    SĐT — probe 2026-08-06); gửi kèm SĐT như mẫu curl trong tài liệu CNV để CNV
    tự khớp đơn về đúng contact.
    """
    customer = getattr(order, "customer", None)
    full_name = getattr(customer, "full_name", "") or getattr(order, "name", "") or ""
    last_name, first_name = split_vietnamese_name(full_name)
    phone = (
        getattr(customer, "phone_normalized", "")
        or getattr(customer, "phone_raw", "")
        or getattr(order, "phone", "")
        or ""
    )
    body = {"last_name": last_name, "first_name": first_name, "phone": phone}
    if customer_cnv_id:
        body["id"] = customer_cnv_id
    return body


def map_order(
    order: Any,
    items: list[Any],
    customer_cnv_id: int | None,
    product_cnv_map: dict[int, dict],
) -> dict:
    """Order + items → payload đơn CNV.

    ``product_cnv_map``: ``{local_product_id: {"id": ..., "variant_id": ...}}``
    lấy từ CnvSyncRecord — sản phẩm nào chưa có mapping thì line item vẫn gửi
    (product_exists=False) để tổng tiền đơn không hụt.
    """
    line_items = []
    for item in items:
        mapping = product_cnv_map.get(item.product_id) or {}
        line_items.append(
            {
                "product_id": mapping.get("id", 0),
                "variant_id": mapping.get("variant_id", 0),
                "product_exists": bool(mapping.get("id")),
                "name": item.product_name,
                "title": item.product_name,
                "variant_title": "Default Title",
                "sku": item.sku or "",
                "quantity": item.quantity,
                "price": _money(item.unit_price),
                "total_discount": 0,
                "requires_shipping": True,
            }
        )

    subtotal = _money(order.total_amount)
    discounts = _money(order.discount_amount)
    total = _money(order.total_after_discount) or max(subtotal - discounts, 0)

    payload: dict = {
        "order": {
            "name": order.order_code or f"#{order.id}",
            "test": False,
            "customer": map_order_customer(order, customer_cnv_id),
            "line_items": line_items,
            "subtotal_price": subtotal,
            "total_discounts": discounts,
            "total_price": total,
            "note": "",
            "created_at": order.created_at.isoformat(),
            "unique_id": build_unique_id(order.id),
            **map_order_status(order.status),
        }
    }
    address = map_shipping_address(order)
    if address:
        payload["order"]["shipping_address"] = address
    return payload


def map_shipping_address(order: Any) -> dict | None:
    if not (order.address or order.raw_address):
        return None
    return {
        "phone": order.phone or "",
        "address1": order.address or order.raw_address,
        "country": "Vietnam",
        "country_code": "VN",
        "province": order.province or "",
        "province_code": order.province_code or "",
        "district": order.district or "",
        "district_code": order.district_code or "",
        "ward": order.ward or "",
        "ward_code": order.ward_code or "",
    }
