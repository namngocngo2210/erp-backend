"""Đồng bộ phí ship invoiceDelivery.price A->B: PUT trực tiếp 1 lần khi lệch.

B (retailer cũ, không tích hợp Shopee) tự suy giá ship theo deliveryCode và bỏ
ShippingSellerProtectionFeeAmount → delete+resync vô nghĩa. Thay bằng PUT giá 1 lần
+ read-back; vẫn lệch thì B-limited, log, không loop.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def sync_delivery_price_once(
    *,
    invoice_code: str,
    a_price,
    b_price,
    put_price: Callable[[str, object], object],
    read_b_price: Callable[[], object],
) -> bool:
    """PUT giá ship đúng 1 lần nếu lệch; read-back. True nếu khớp (hoặc đã khớp sẵn)."""
    if a_price == b_price:
        return True
    put_price(invoice_code, a_price)
    new_b = read_b_price()
    if new_b == a_price:
        return True
    logger.warning(
        "ship B-limited %s: A=%s B=%s (after PUT)", invoice_code, a_price, new_b,
    )
    return False
