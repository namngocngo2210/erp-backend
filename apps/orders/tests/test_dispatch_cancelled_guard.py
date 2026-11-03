"""Chốt cuối: `dispatch_orders_batch` là điểm THẬT SỰ trừ tồn → đơn hủy tuyệt đối không được xuất.

Từ lúc quét tới lúc bấm "Xác nhận" có thể cách vài phút (và luồng cron khớp mã pending
không đi qua lookup) nên guard ở lookup (Task 2) là chưa đủ.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import StockSalesService
from apps.inventory.models import MovementType, StockMovement
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.tests.helpers import no_sale_movement
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

# Verifier được import cục bộ trong hàm → patch thẳng trên class ở module nguồn.
_VERIFY = (
    "apps.kiotviet.application.services.cancellation_verifier."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


@pytest.fixture
def actor():
    return make_admin("dispatch-guard-actor")


@pytest.fixture
def channel():
    return Channel.objects.create(code="dispatch-guard", name="Dispatch Guard")


@pytest.fixture
def product():
    return Product.objects.create(name="SP Guard", sku="SKU-GUARD-1")


@pytest.fixture
def warehouse(product):
    wh = Warehouse.objects.create(code="WH-GUARD", name="Kho Guard")
    StockCommandService.record_movement(
        warehouse_id=wh.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    return wh


def _make_order(channel: Channel, product: Product, warehouse: Warehouse, **kwargs) -> Order:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer.objects.create(
        full_name=f"KH {suffix}",
        phone_raw=f"09{suffix[:8]}",
        phone_normalized=f"09{suffix[:8]}",
        code=f"CUST-GUARD-{suffix}",
    )
    kwargs.setdefault("status", OrderStatus.IN_DELIVERY)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code=f"ORD-{suffix}",
        name=f"KH {suffix}",
        phone=f"09{suffix[:8]}",
        total_amount="200000",
        fulfillment_warehouse=warehouse,
        **kwargs,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=2,
        unit_price=100000,
        line_total=200000,
    )
    return order


def test_db_cancelled_order_is_rejected_without_touching_stock(channel, product, warehouse, actor):
    order = _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"
    assert no_sale_movement(order)


def test_db_cancelled_order_does_not_call_verifier(channel, product, warehouse, actor):
    """DB check chạy TRƯỚC → không tốn round-trip KiotViet."""
    order = _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED)
    with patch(_VERIFY) as verify:
        OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    verify.assert_not_called()


def test_invoice_status_2_is_rejected(channel, product, warehouse, actor):
    order = _make_order(channel, product, warehouse, invoice_status=2)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["error"] == "order_cancelled"
    assert no_sale_movement(order)


def test_kiotviet_cancelled_is_rejected_even_if_db_looks_fine(channel, product, warehouse, actor):
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"
    assert no_sale_movement(order)


def test_verify_unavailable_is_fail_closed_batch(channel, product, warehouse, actor):
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is False
    assert results[0]["error"] == "verify_unavailable"
    assert no_sale_movement(order)


def test_live_order_still_dispatches_batch(channel, product, warehouse, actor):
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is True
    assert not no_sale_movement(order)


def test_unverifiable_with_clean_db_still_dispatches_batch(channel, product, warehouse, actor):
    """Đơn ngoài KiotViet / kill-switch tắt → DB là nguồn sự thật, không chặn oan."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is True
    assert not no_sale_movement(order)


def test_order_cancelled_during_verification_is_blocked_batch(channel, product, warehouse, actor):
    """TOCTOU: webhook hủy đơn ĐÚNG trong cửa sổ gọi HTTP (vài trăm ms → 3s).

    Instance `order` được fetch TRƯỚC khi verify → nếu guard trong transaction chỉ đọc bộ
    nhớ thì nó không thấy đơn đã hủy và vẫn trừ tồn. Guard phải đọc lại DB.
    """
    order = _make_order(channel, product, warehouse)

    def _verify(target_order):
        # Webhook KiotViet chen vào giữa lúc ta đang chờ mạng (chỉ đụng DB, không đụng
        # instance đang giữ trong bộ nhớ).
        Order.objects.filter(id=target_order.id).update(status=OrderStatus.CANCELLED)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"
    assert no_sale_movement(order)


def test_bogus_verdict_cannot_disable_the_guard(channel, product, warehouse, actor):
    """Guard không tin caller: verdict không phải CancelCheck hợp lệ → vẫn hỏi KiotViet."""
    from apps.orders.exceptions import OrderCancelledError

    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED) as verify:
        with pytest.raises(OrderCancelledError):
            OrderService.update_order_inline(
                order,
                {"crm": {"delivery_business_status_code": "dispatched"}},
                actor,
                verdict=True,  # caller "khai" đã verify — không được chấp nhận
            )
    verify.assert_called_once()
    assert verify.call_args.args[0].id == order.id
    assert no_sale_movement(order)


def test_already_dispatched_order_skips_kiotviet_roundtrip(channel, product, warehouse, actor):
    """Đơn ĐÃ xuất kho → không hỏi lại KiotViet (tồn đã trừ, round-trip chỉ làm chậm lô quét).

    Kho quét 50 kiện rồi bấm Xác nhận: nếu mỗi đơn (kể cả đơn đã xuất) đều gọi KiotViet
    TUẦN TỰ thì request kéo dài hàng phút và ăn hết web worker.
    """
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        first = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert first[0]["ok"] is True
    assert verify.call_count == 1

    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        second = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    verify.assert_not_called()
    assert second[0]["ok"] is True
    assert "already_dispatched" in second[0]["warnings"]
    # Idempotent: không trừ tồn lần hai.
    assert StockMovement.objects.filter(
        reference_type="order",
        reference_id=order.id,
        movement_type=MovementType.SALE,
    ).count() == 1


def test_already_dispatched_but_cancelled_in_db_is_still_blocked(channel, product, warehouse, actor):
    """Bỏ round-trip KHÔNG có nghĩa là bỏ guard: check DB tươi vẫn chặn đơn hủy."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    Order.objects.filter(id=order.id).update(status=OrderStatus.CANCELLED)

    with patch(_VERIFY) as verify:
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    verify.assert_not_called()
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"


def test_guard_is_per_order_not_whole_batch(channel, product, warehouse, actor):
    """Đơn hủy bị chặn nhưng đơn sống trong cùng lô vẫn xuất bình thường."""
    dead = _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED)
    live = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        results = OrderService.dispatch_orders_batch(
            order_ids=[dead.id, live.id], warehouse_id=warehouse.id, actor=actor
        )
    by_id = {r["order_id"]: r for r in results}
    assert by_id[dead.id]["ok"] is False
    assert by_id[dead.id]["error"] == "order_cancelled"
    assert by_id[live.id]["ok"] is True
    assert no_sale_movement(dead)
    assert not no_sale_movement(live)


def test_undispatched_order_is_not_already_dispatched_and_verify_still_runs(
    channel, product, warehouse, actor
):
    """SALE + SALE_REVERSAL (net = 0, huỷ → un-dispatch → mở lại) không được coi là
    already_dispatched — guard cũ chỉ đếm exists(SALE) sẽ bỏ qua round-trip KiotViet
    oan cho đơn đã bị un-dispatch nhưng chưa từng bị huỷ tuyệt đối lần 2."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        first = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    assert first[0]["ok"] is True
    assert verify.call_count == 1

    # Un-dispatch thật (kho bỏ trạng thái "Đã xuất kho"): hoàn hết outstanding về 0 VÀ
    # đưa trạng thái giao hàng về "Kho đang xử lý". Đi đúng đường nghiệp vụ chứ không
    # gọi thẳng release_for_order, vì cờ already_dispatched đọc CẢ HAI tín hiệu.
    OrderService.update_order_inline(
        order,
        {"crm": {"delivery_business_status_code": "warehouse_processing"}},
        actor,
    )
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order.id,
        movement_type=MovementType.SALE_REVERSAL,
    ).exists()

    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        second = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    # Net = 0 → KHÔNG được coi là already_dispatched, verify phải chạy lại (đây là
    # phần bị fix — guard cũ chỉ đếm exists(SALE) sẽ set already=True và bỏ qua dòng này).
    verify.assert_called_once()
    assert verify.call_args.args[0].id == order.id
    assert "already_dispatched" not in second[0]["warnings"]
    assert second[0]["ok"] is True


def test_dispatched_state_without_ledger_warns_but_still_verifies(
    channel, product, warehouse, actor
):
    """Sổ tồn trống mà trạng thái vẫn "Đã xuất kho" → phải cảnh báo, và VẪN verify.

    Ca thật: đơn không khớp được sản phẩm nào (xuất kho với `lines=[]` → không có bút
    toán), hoặc đơn xuất lại bị bỏ rơi sổ. Trước fix, lượt quét thứ hai đi lọt không
    một tiếng động: lookup báo chưa xuất, bấm Xác nhận trả dấu ✓ y như lần đầu dù
    không ghi thêm gì. Cảnh báo KHÔNG được kéo theo việc bỏ round-trip xác minh —
    nhánh đó chỉ an toàn khi tồn đã trừ thật.
    """
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    # Sổ về 0 nhưng trạng thái giao hàng giữ nguyên "dispatched".
    StockSalesService.release_for_order(order_id=order.id)

    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        second = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    verify.assert_called_once()
    assert verify.call_args.args[0].id == order.id
    assert second[0]["ok"] is True
    assert "already_dispatched" in second[0]["warnings"]


def test_undispatched_order_blocked_by_kiotviet_is_not_silently_allowed(
    channel, product, warehouse, actor
):
    """Đơn un-dispatch (net=0) rồi bị huỷ trên KiotViet: vì verify VẪN chạy (không bị
    guard already_dispatched bỏ qua), đơn phải bị chặn — đây là hệ quả trực tiếp của
    fix NET, chốt bằng test riêng cho rõ ràng."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    StockSalesService.release_for_order(order_id=order.id)

    with patch(_VERIFY, return_value=CancelCheck.CANCELLED) as verify:
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=actor
        )
    verify.assert_called_once()
    assert verify.call_args.args[0].id == order.id
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"
    # Không xuất lại: vẫn chỉ có SALE movement gốc, không có cái mới.
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order.id,
        movement_type=MovementType.SALE,
    ).count() == 1


# ---------------------------------------------------------------------------
# Cửa hậu xuất kho: PATCH /api/orders/<id> (dropdown "Đã xuất kho") trừ tồn THẲNG
# qua ``update_order_inline`` — không đi qua ``dispatch_orders_batch`` nên phải có
# guard riêng: đơn hủy (DB hoặc KiotViet) tuyệt đối không được xuất kho.
# (Gộp từ test_inline_dispatch_cancelled_guard.py, 2026-08-05.)
# ---------------------------------------------------------------------------

_PAYLOAD = {"crm": {"delivery_business_status_code": "dispatched"}}


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=make_admin("inline-guard-admin"))
    return api


def _patch_dispatched(client: APIClient, order: Order):
    return client.patch(f"/api/orders/{order.id}", _PAYLOAD, format="json")


def test_db_cancelled_order_cannot_be_marked_dispatched(client, channel, product, warehouse):
    order = _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = _patch_dispatched(client, order)
    assert res.status_code in (403, 409)
    assert no_sale_movement(order)


def test_invoice_status_2_cannot_be_marked_dispatched(client, channel, product, warehouse):
    order = _make_order(channel, product, warehouse, invoice_status=2)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = _patch_dispatched(client, order)
    assert res.status_code in (403, 409)
    assert no_sale_movement(order)


def test_kiotviet_cancelled_is_blocked_even_if_db_looks_fine(client, channel, product, warehouse):
    """DB sạch nhưng KiotViet đã hủy (sync trễ tới 30') → vẫn phải chặn."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        res = _patch_dispatched(client, order)
    assert res.status_code == 409
    assert "hủy" in res.json()["detail"]
    assert no_sale_movement(order)


def test_verify_unavailable_is_fail_closed_inline(client, channel, product, warehouse):
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        res = _patch_dispatched(client, order)
    assert res.status_code == 503
    assert "KiotViet" in res.json()["detail"]
    assert no_sale_movement(order)


def test_live_order_still_dispatches_inline(client, channel, product, warehouse):
    """Đối chứng dương: đơn hợp lệ vẫn trừ tồn như cũ (không chặn oan)."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = _patch_dispatched(client, order)
    assert res.status_code == 200
    assert not no_sale_movement(order)


def test_unverifiable_with_clean_db_still_dispatches_inline(client, channel, product, warehouse):
    """Đơn ngoài KiotViet / kill-switch tắt → DB là nguồn sự thật, không chặn oan."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = _patch_dispatched(client, order)
    assert res.status_code == 200
    assert not no_sale_movement(order)


def test_verifier_runs_outside_the_write_transaction(client, channel, product, warehouse):
    """HTTP KiotViet KHÔNG được nằm trong transaction ghi sổ: mỗi atomic do code sản phẩm
    mở sẽ thêm 1 savepoint (test đã chạy trong atomic của pytest-django) → độ sâu savepoint
    lúc verifier chạy phải bằng đúng độ sâu lúc chưa vào view."""
    order = _make_order(channel, product, warehouse)
    baseline = len(connection.savepoint_ids)
    depths: list[int] = []

    def _verify(_order):
        depths.append(len(connection.savepoint_ids))
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        res = _patch_dispatched(client, order)

    assert res.status_code == 200
    assert depths, "verifier phải được gọi (nếu không, guard đơn hủy đã bị bỏ qua)"
    assert all(d == baseline for d in depths), (
        f"verifier gọi bên trong transaction (savepoint depth {depths} != {baseline})"
    )


def test_order_cancelled_during_verification_is_blocked_inline(client, channel, product, warehouse):
    """TOCTOU: webhook hủy đơn trong lúc ta đang chờ KiotViet → guard đọc DB tươi phải chặn."""
    order = _make_order(channel, product, warehouse)

    def _verify(target_order):
        Order.objects.filter(id=target_order.id).update(status=OrderStatus.CANCELLED)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        res = _patch_dispatched(client, order)

    assert res.status_code == 409
    assert no_sale_movement(order)


def test_other_inline_edits_do_not_call_verifier(client, channel, product, warehouse):
    """Guard chỉ chạy ở nhánh chuyển sang 'dispatched' — sửa đơn thường không đụng KiotViet."""
    order = _make_order(channel, product, warehouse)
    with patch(_VERIFY) as verify:
        res = client.patch(f"/api/orders/{order.id}", {"name": "Tên mới"}, format="json")
    assert res.status_code == 200
    verify.assert_not_called()


# ---------------------------------------------------------------------------
# Perf lô xuất kho: load đơn 1 lần theo id__in (không 2 round-trip/đơn) và
# prefetch verdict KiotViet song song trước vòng lặp ghi sổ. Hai tối ưu này
# KHÔNG được đổi kết quả per-order — các test dưới chốt cả hai chiều.
# ---------------------------------------------------------------------------


def test_batch_load_queries_do_not_scale_with_batch_size(channel, product, warehouse, actor):
    """Lô to hay lô nhỏ, phần LOAD đơn phải cùng số query (id__in, không N+1).

    Dùng toàn đơn hủy-theo-DB: vòng lặp chính early-exit trước mọi query per-order
    (sổ tồn, verify, ghi sổ) nên số query đo được chỉ còn phần load — chính là phần
    bị N+1 trước fix (2 query/đơn). Gọi warm-up một lần trước block đo để các cache
    lazy (wiring port, ContentType, RBAC...) không lệch vào phép so sánh — bẫy đã
    biết ở các test đếm query khác của repo.
    """
    from django.test.utils import CaptureQueriesContext

    small = [
        _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED) for _ in range(2)
    ]
    large = [
        _make_order(channel, product, warehouse, status=OrderStatus.CANCELLED) for _ in range(5)
    ]
    with patch(_VERIFY) as verify:
        OrderService.dispatch_orders_batch(
            order_ids=[small[0].id], warehouse_id=warehouse.id, actor=actor
        )
        with CaptureQueriesContext(connection) as ctx_small:
            results_small = OrderService.dispatch_orders_batch(
                order_ids=[o.id for o in small], warehouse_id=warehouse.id, actor=actor
            )
        with CaptureQueriesContext(connection) as ctx_large:
            results_large = OrderService.dispatch_orders_batch(
                order_ids=[o.id for o in large], warehouse_id=warehouse.id, actor=actor
            )
    verify.assert_not_called()
    assert [r["error"] for r in results_small] == ["order_cancelled"] * 2
    assert [r["error"] for r in results_large] == ["order_cancelled"] * 5
    assert len(ctx_small.captured_queries) == len(ctx_large.captured_queries), (
        f"load phase N+1: lô 2 đơn = {len(ctx_small.captured_queries)} query, "
        f"lô 5 đơn = {len(ctx_large.captured_queries)} query"
    )


def test_prefetch_calls_verifier_once_per_order_and_keeps_result_order(
    channel, product, warehouse, actor
):
    """Lô N đơn cần verify → verifier được gọi đúng N lần (mỗi đơn một lần, qua
    prefetch song song), kết quả trả về giữ nguyên thứ tự order_ids đầu vào."""
    orders = [_make_order(channel, product, warehouse) for _ in range(4)]
    seen: list[int] = []

    def _verify(order):
        seen.append(order.id)  # list.append an toàn dưới GIL, gọi được từ nhiều thread
        return CancelCheck.OK

    order_ids = [o.id for o in orders]
    with patch(_VERIFY, side_effect=_verify):
        results = OrderService.dispatch_orders_batch(
            order_ids=order_ids, warehouse_id=warehouse.id, actor=actor
        )
    assert sorted(seen) == sorted(order_ids)
    assert len(seen) == len(orders)
    assert [r["order_id"] for r in results] == order_ids
    assert all(r["ok"] for r in results)
    for order in orders:
        assert not no_sale_movement(order)


def test_prefetch_verify_error_fails_only_that_order(channel, product, warehouse, actor):
    """1 mã verify lỗi (fail-closed) → CHỈ đơn đó fail, đơn khác trong lô vẫn xuất."""
    good_first = _make_order(channel, product, warehouse)
    bad = _make_order(channel, product, warehouse)
    good_last = _make_order(channel, product, warehouse)

    def _verify(order):
        if order.id == bad.id:
            raise VerificationUnavailable("timeout")
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        results = OrderService.dispatch_orders_batch(
            order_ids=[good_first.id, bad.id, good_last.id],
            warehouse_id=warehouse.id,
            actor=actor,
        )
    by_id = {r["order_id"]: r for r in results}
    assert by_id[bad.id]["ok"] is False
    assert by_id[bad.id]["error"] == "verify_unavailable"
    assert by_id[good_first.id]["ok"] is True
    assert by_id[good_last.id]["ok"] is True
    assert no_sale_movement(bad)
    assert not no_sale_movement(good_first)
    assert not no_sale_movement(good_last)


def test_prefetch_cancelled_verdict_blocks_only_that_order(channel, product, warehouse, actor):
    """Verdict CANCELLED từ prefetch map đúng về đơn của nó, không lây sang đơn khác."""
    dead = _make_order(channel, product, warehouse)
    live = _make_order(channel, product, warehouse)

    def _verify(order):
        return CancelCheck.CANCELLED if order.id == dead.id else CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        results = OrderService.dispatch_orders_batch(
            order_ids=[dead.id, live.id], warehouse_id=warehouse.id, actor=actor
        )
    by_id = {r["order_id"]: r for r in results}
    assert by_id[dead.id]["ok"] is False
    assert by_id[dead.id]["error"] == "order_cancelled"
    assert by_id[live.id]["ok"] is True
    assert no_sale_movement(dead)
    assert not no_sale_movement(live)
