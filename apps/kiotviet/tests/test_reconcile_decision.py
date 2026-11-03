from apps.kiotviet.infrastructure.services._reconcile_decision import (
    decide_reconcile_action,
    expected_b_state,
)
from apps.kiotviet.infrastructure.services.reconcile import RECONCILE_MODES


def test_normal_case_mirrors_a_exactly():
    assert expected_b_state(3, 2, exact_mirror=False) == (3, 2)
    assert expected_b_state(1, 3, exact_mirror=False) == (1, 3)


def test_exception_delivered_but_processing_default_caps_delivery():
    # A(inv=3, del=3): default False -> giữ invoice 3, cap delivery về 2 (tránh auto-complete)
    assert expected_b_state(3, 3, exact_mirror=False) == (3, 2)


def test_exception_delivered_but_processing_exact_mirror_accepts_forced_complete():
    # exact_mirror True: del=3, chấp nhận KiotViet ép invoice->1
    assert expected_b_state(3, 3, exact_mirror=True) == (1, 3)


def test_no_delivery_maps_to_completed_delivered():
    # Đơn không có giao hàng (delivery_status None) -> B Giao thành công(3) + Hoàn thành(1),
    # không phụ thuộc invoice A hay cờ.
    assert expected_b_state(3, None, exact_mirror=False) == (1, 3)
    assert expected_b_state(1, None, exact_mirror=False) == (1, 3)
    assert expected_b_state(None, None, exact_mirror=True) == (1, 3)


def _a(inv=3, dele=2, total=1000, price=20):
    return {"invoice_status": inv, "delivery_status": dele, "total": total, "delivery_price": price}


def _b(inv=3, dele=2, total=1000, price=20):
    return {"invoice_status": inv, "delivery_status": dele, "total": total, "delivery_price": price}


def test_no_mirror_returns_create():
    assert decide_reconcile_action(a=_a(), b=None, exact_mirror=False) == "create"


def test_all_match_returns_skip():
    assert decide_reconcile_action(a=_a(), b=_b(), exact_mirror=False) == "skip"


def test_total_within_1d_is_skip():
    assert decide_reconcile_action(a=_a(total=1000), b=_b(total=1001), exact_mirror=False) == "skip"


def test_total_diff_over_1d_recreate():
    assert decide_reconcile_action(a=_a(total=1000), b=_b(total=1002), exact_mirror=False) == "delete_recreate"


def test_delivery_price_mismatch_recreate():
    assert decide_reconcile_action(a=_a(price=20), b=_b(price=25), exact_mirror=False) == "delete_recreate"


def test_invoice_status_mismatch_recreate():
    assert decide_reconcile_action(a=_a(inv=1, dele=3), b=_b(inv=3, dele=3), exact_mirror=False) == "delete_recreate"


def test_exception_default_b_capped_is_skip():
    # A(inv=3, del=3) default -> expected B (inv=3, del=2). B đúng vậy -> skip, KHÔNG churn.
    assert decide_reconcile_action(a=_a(inv=3, dele=3), b=_b(inv=3, dele=2), exact_mirror=False) == "skip"


def test_exception_exact_mirror_forced_complete_is_skip():
    # exact_mirror True -> expected B (inv=1, del=3). KiotViet ép B=(1,3) -> skip, KHÔNG churn.
    assert decide_reconcile_action(a=_a(inv=3, dele=3), b=_b(inv=1, dele=3), exact_mirror=True) == "skip"


def test_no_delivery_on_a_with_none_b_returns_create():
    # A không có delivery block (delivery_status=None) nhưng B chưa tồn tại -> vẫn "create".
    assert decide_reconcile_action(a=_a(dele=None), b=None, exact_mirror=False) == "create"


def test_no_delivery_a_with_b_completed_delivered_is_skip():
    # Đơn không giao hàng: đích B = (inv=1, del=3). B đúng vậy -> skip, KHÔNG churn.
    assert decide_reconcile_action(a=_a(dele=None), b=_b(inv=1, dele=3), exact_mirror=False) == "skip"


def test_no_delivery_a_with_b_still_in_delivery_recreate():
    # Đơn không giao hàng mà B còn ở Đang giao(2)/chưa hoàn thành -> đẩy lên Giao thành công
    # bằng xoá + tạo lại.
    assert decide_reconcile_action(a=_a(dele=None), b=_b(inv=3, dele=2), exact_mirror=False) == "delete_recreate"


# --- RECONCILE_MODES ----------------------------------------------------------


def test_modes_are_three_ending_in_reconcile_sync():
    names = [m[0] for m in RECONCILE_MODES]
    assert names == ["purge_cancelled", "purge_duplicates", "reconcile_sync"]
    assert RECONCILE_MODES[-1][1] == {"reconcile_sync": True}
