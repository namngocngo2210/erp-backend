"""Quyết định reconcile — hàm THUẦN (không I/O), unit-test được không cần KiotViet.

expected_b_state: đích B suy từ A, xử lý ca ngoại lệ A(giao thành công + đang xử lý) theo cờ.
decide_reconcile_action: so B hiện tại với đích → create / delete_recreate / skip.
"""
from __future__ import annotations

# Ca ngoại lệ DUY NHẤT: A đã "Giao thành công" (delivery=3) nhưng hóa đơn còn "Đang xử lý"
# (invoice=3). Set delivery=3 trên B khiến KiotViet auto-complete invoice->1 != A.
_DELIVERED = 3
_PROCESSING = 3
_COMPLETED = 1
_IN_DELIVERY = 2


def expected_b_state(
    a_invoice_status: int | None,
    a_delivery_status: int | None,
    *,
    exact_mirror: bool,
) -> tuple[int | None, int | None]:
    """(exp_invoice, exp_delivery) mà B nên đạt. Mặc định mirror A; đơn không giao hàng ->
    Giao thành công; riêng ca A(inv=3, del=3) xử lý theo cờ để tránh hoàn-thành-giả + churn."""
    # Đơn KHÔNG có giao hàng (bán tại quầy / nhận tại chỗ, không vận chuyển) coi như đã xong
    # -> B Giao thành công (delivery=3); KiotViet auto-complete hóa đơn -> Hoàn thành(1).
    if a_delivery_status is None:
        return (_COMPLETED, _DELIVERED)
    if a_delivery_status == _DELIVERED and a_invoice_status == _PROCESSING:
        if exact_mirror:
            # Chấp nhận KiotViet ép invoice->1 khi set delivery=3.
            return (_COMPLETED, _DELIVERED)
        # An toàn sổ sách: giữ invoice=3, cap delivery về Đang giao(2).
        return (_PROCESSING, _IN_DELIVERY)
    return (a_invoice_status, a_delivery_status)


_TOTAL_TOLERANCE = 1


def _to_int(v) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def decide_reconcile_action(*, a: dict, b: dict | None, exact_mirror: bool) -> str:
    """So B với đích suy từ A. None mirror -> create; lệch bất kỳ chiều -> delete_recreate; khớp -> skip."""
    if b is None:
        return "create"

    exp_inv, exp_del = expected_b_state(
        a.get("invoice_status"), a.get("delivery_status"), exact_mirror=exact_mirror
    )

    total_ok = abs(_to_int(a.get("total")) - _to_int(b.get("total"))) <= _TOTAL_TOLERANCE
    price_ok = _to_int(a.get("delivery_price")) == _to_int(b.get("delivery_price"))
    inv_ok = b.get("invoice_status") == exp_inv
    # exp_del hiện luôn có giá trị (no-delivery -> 3); guard `is None` giữ phòng thủ, vô hại.
    del_ok = exp_del is None or b.get("delivery_status") == exp_del

    if total_ok and price_ok and inv_ok and del_ok:
        return "skip"
    return "delete_recreate"
