"""Nhận diện đơn XUẤT LẠI (`<mã>.NN`) của một đơn bị huỷ.

Tách khỏi ``subscribers`` để hai đường xử lý dùng CHUNG một luật: bắt-tại-chỗ
lúc webhook huỷ về (``_on_order_status_changed``) và quét-bù định kỳ
(``ReissueLedgerRepairService``). Luật lệch nhau giữa hai đường là cách chắc
chắn nhất để sinh ra một lớp lỗi mới.
"""
from __future__ import annotations

import re

# KiotViet "hủy → xuất lại hóa đơn" sinh mã `<gốc>.NN` — cùng regex nhận diện
# với webhook reattach (apps.kiotviet.webhook_impl).
_REISSUE_SUFFIX_RE = re.compile(r"\.\d{1,3}$")

CANCELLED_STATUS = 6


def invoice_base(code: str | None) -> str:
    return _REISSUE_SUFFIX_RE.sub("", (code or "").strip())


def find_cancelled_source_order(order_id: int) -> int | None:
    """Đơn bị huỷ mà đơn này đã nhận sổ xuất — chiều NGƯỢC của hàm dưới.

    Dùng chung luật nhận diện (cùng mã vận đơn + cùng gốc hoá đơn sau khi bỏ hậu
    tố) để backfill không bắt được cặp nào khác với đường ghi. Chuỗi huỷ nhiều
    lần (`X` → `X.01` → `X.02`) lấy đơn huỷ MỚI NHẤT — đó là đơn vừa giao sổ.
    """
    from apps.orders.models import Order

    order = (
        Order.objects.filter(pk=order_id)
        .only("id", "shipping_code", "invoice_code")
        .first()
    )
    if order is None:
        return None
    shipping_code = (order.shipping_code or "").strip()
    base = invoice_base(order.invoice_code)
    if not shipping_code or not base:
        return None
    candidates = (
        Order.objects.filter(
            deleted_at__isnull=True,
            shipping_code=shipping_code,
            status=CANCELLED_STATUS,
        )
        .exclude(pk=order_id)
        .order_by("-id")
        .values("id", "invoice_code")
    )
    for candidate in candidates:
        if invoice_base(candidate["invoice_code"]) == base:
            return candidate["id"]
    return None


def find_live_reissued_sibling(order_id: int) -> int | None:
    """Đơn xuất-lại (`<mã>.NN` cùng gốc hoá đơn, CÙNG mã vận đơn) đang sống.

    Chỉ nhận khi hoá đơn 2 đơn cùng gốc sau khi bỏ hậu tố `.NN` — trùng mỗi mã
    vận đơn không đủ (prod từng có 2 đơn khác nhau dùng chung mã). Đơn anh em
    đã có sổ xuất riêng hay chưa do caller tự kiểm trong lock — ở đây không
    check trước để tránh TOCTOU.
    """
    from apps.orders.models import Order

    order = (
        Order.objects.filter(pk=order_id)
        .only("id", "shipping_code", "invoice_code")
        .first()
    )
    if order is None:
        return None
    shipping_code = (order.shipping_code or "").strip()
    base = invoice_base(order.invoice_code)
    if not shipping_code or not base:
        return None
    siblings = (
        Order.objects.filter(deleted_at__isnull=True, shipping_code=shipping_code)
        .exclude(pk=order_id)
        .exclude(status=CANCELLED_STATUS)
        .order_by("-id")
        .values("id", "invoice_code")
    )
    for sib in siblings:
        if invoice_base(sib["invoice_code"]) == base:
            return sib["id"]
    return None
