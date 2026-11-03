"""Lookup mã vận đơn (/api/orders/lookup-by-shipping-code) — gộp 2026-08-05.

Gồm 3 cụm: xác minh hủy KiotViet realtime khi quét xuất, cờ already_dispatched
chặn quét trùng, và pending scan khi mã chưa khớp đơn nào.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    ReturnStatus,
)
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderRecallEntry, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin, make_user_with_sections
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

_URL = "/api/orders/lookup-by-shipping-code"
# Từ Lô 3, view gọi KiotViet qua `kiotviet_cancellation_port` chứ không import
# `InvoiceCancellationVerifier` nữa → patch thẳng trên class ở module nguồn, cùng
# cách `test_dispatch_cancelled_guard` vẫn làm. Adapter bind lười lúc gọi nên patch
# này chặn đúng, và nó dịch `CancelCheck` / `VerificationUnavailable` bên dưới sang
# kiểu của orders — test không cần biết.
_VERIFY = (
    "apps.kiotviet.application.services.cancellation_verifier."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


# ---------------------------------------------------------------------------
# Quét XUẤT giao phải hỏi KiotViet realtime trước khi cho xuất kho (DB trễ tới 30').
# (Gộp từ test_shipping_lookup_cancel_verify.py.)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=make_user_with_sections("scanverify_staff", ["shipments"]))
    return api


@pytest.fixture
def channel():
    return Channel.objects.create(code="scanverify", name="Scan Verify")


def _customer() -> Customer:
    customer, _ = Customer.objects.get_or_create(
        phone_normalized="0900000002",
        defaults={"full_name": "KH", "phone_raw": "0900000002"},
    )
    return customer


def _order(channel: Channel, **kwargs) -> Order:
    kwargs.setdefault("status", OrderStatus.IN_DELIVERY)
    kwargs.setdefault("shipping_code", f"SC{uuid.uuid4().hex[:10]}")
    return Order.objects.create(
        channel=channel,
        customer=_customer(),
        order_code=f"ORD-{uuid.uuid4().hex[:10]}",
        name="KH",
        phone="0900000002",
        **kwargs,
    )


def test_dispatch_mode_blocks_when_kiotviet_says_cancelled(client, channel):
    order = _order(channel)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 409
    assert res.json()["detail"] == "order_cancelled"
    assert res.json()["order_code"] == order.order_code


def test_dispatch_mode_blocks_when_kiotviet_unreachable(client, channel):
    order = _order(channel)
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 503
    assert res.json()["detail"] == "verify_unavailable"


def test_dispatch_mode_allows_live_order(client, channel):
    order = _order(channel)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 200
    assert res.json()["order_id"] == order.id


def test_dispatch_mode_is_default(client, channel):
    """Không truyền mode → mặc định dispatch → vẫn phải xác minh."""
    order = _order(channel)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        res = client.get(_URL, {"code": order.shipping_code})
    assert res.status_code == 409


def test_unverifiable_falls_back_to_db_cancelled_status(client, channel):
    """Đơn ngoài KiotViet / kill-switch tắt → DB là nguồn sự thật (status=6)."""
    order = _order(channel, status=OrderStatus.CANCELLED)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = client.get(_URL, {"code": order.shipping_code})
    assert res.status_code == 409
    assert res.json()["detail"] == "order_cancelled"


def test_unverifiable_falls_back_to_db_invoice_status(client, channel):
    """invoice_status=2 (hóa đơn hủy) cũng là đơn hủy dù Order.status chưa đổi."""
    order = _order(channel, invoice_status=2)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = client.get(_URL, {"code": order.shipping_code})
    assert res.status_code == 409


def test_unverifiable_allows_live_db_order(client, channel):
    order = _order(channel)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        res = client.get(_URL, {"code": order.shipping_code})
    assert res.status_code == 200
    assert res.json()["order_id"] == order.id


def _two_orders_same_code(channel: Channel, *, old_status, new_status) -> tuple[Order, Order, str]:
    """Đơn CŨ + đơn MỚI dùng chung 1 mã vận đơn (mã tái sử dụng)."""
    code = f"SC{uuid.uuid4().hex[:10]}"
    old = _order(channel, status=old_status)
    old.shipping_code = code
    old.save(update_fields=["shipping_code"])
    new = _order(channel, status=new_status)
    new.shipping_code = code
    new.save(update_fields=["shipping_code"])
    return old, new, code


def test_multiple_orders_verifies_only_the_newest(client, channel):
    """1 mã khớp 2 đơn: đơn CŨ đã hủy, đơn MỚI hợp lệ → KHÔNG chặn.

    Chặn ở đây là chặn OAN: cron (`_process_one`) lấy đơn mới nhất và xuất bình thường,
    còn kho thì bị 409 không có đường thoát. Đơn hủy vẫn bị chặn ở chốt cuối dispatch.
    """
    old, new, code = _two_orders_same_code(
        channel, old_status=OrderStatus.CANCELLED, new_status=OrderStatus.IN_DELIVERY
    )
    verified: list[int] = []

    def _verify(order):
        verified.append(order.id)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        res = client.get(_URL, {"code": code})

    assert verified == [new.id]  # chỉ hỏi KiotViet cho đơn mới nhất
    assert res.status_code == 200
    body = res.json()
    assert body["multiple"] is True
    assert {o["order_id"] for o in body["orders"]} == {old.id, new.id}
    assert body["orders"][0]["order_id"] == new.id  # đơn mới nhất đứng đầu (như cron)


def test_multiple_orders_blocked_when_newest_is_cancelled(client, channel):
    """Đơn MỚI NHẤT (đơn cron sẽ xuất) bị hủy → vẫn 409."""
    _old, new, code = _two_orders_same_code(
        channel, old_status=OrderStatus.IN_DELIVERY, new_status=OrderStatus.IN_DELIVERY
    )
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        res = client.get(_URL, {"code": code})
    assert res.status_code == 409
    assert res.json()["order_code"] == new.order_code


# ---------------------------------------------------------------------------
# Task 7: quét XUẤT phải tra được đơn theo MÃ HOÁ ĐƠN (bán trực tiếp không có mã
# vận đơn). Mã vận đơn vẫn thắng tuyệt đối; mode=return/return_scan không đổi.
# ---------------------------------------------------------------------------


def test_dispatch_mode_matches_invoice_code(client, channel):
    order = _order(channel, shipping_code="", invoice_code="HD-INV-001")
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(
            _URL, {"code": "HD-INV-001", "mode": "dispatch", "invoice_match": "1"}
        )
    assert res.status_code == 200
    body = res.json()
    assert body["order_id"] == order.id
    assert body["matched_by"] == "invoice"
    assert body["fulfillment_warehouse_id"] == order.fulfillment_warehouse_id


# ---------------------------------------------------------------------------
# Bản vá review 29/08/2026: `invoice_match=1` là cửa khoá triển khai — một tab
# quét cũ (bundle trước khi FE roll) không biết gửi tham số này nên KHÔNG BAO
# GIỜ nhận một đơn khớp theo mã hoá đơn, dù backend đã roll xong. Chỉ gửi thêm
# param, không được nới lỏng bất kỳ luật nào khác (mã vận đơn vẫn thắng tuyệt
# đối, return/return_scan vẫn không bao giờ khớp mã hoá đơn).
# ---------------------------------------------------------------------------


def test_dispatch_mode_without_param_ignores_invoice_code(client, channel):
    """Hành vi bundle CŨ: không gửi invoice_match → mã hoá đơn coi như không tồn tại."""
    order = _order(channel, shipping_code="", invoice_code="HD-GATE-001")
    resp = client.get(_URL, {"code": "HD-GATE-001", "mode": "dispatch"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "not_found"
    assert order.invoice_code == "HD-GATE-001"


def test_shipping_code_wins_over_invoice_code(client, channel):
    """Mã trùng shipping_code đơn A và invoice_code đơn B → nhánh multiple, A đứng đầu.

    Cùng cấu trúc với đụng shipping/recall hiện có (test_lookup_code_matching_both_
    fields_returns_multiple): 2 đơn khác nhau cùng mã vẫn hiện cả hai cho kho xử lý,
    nhưng thứ tự ưu tiên (mã vận đơn thắng) quyết định ai đứng đầu / được verify.
    Chạy VỚI invoice_match=1 (không phải chỉ có nó thì shipping mới thắng) để khoá:
    cờ chỉ MỞ nhánh invoice ra, không đổi thứ tự ưu tiên giữa 2 nhánh.
    """
    code = f"DUP-{uuid.uuid4().hex[:8]}"
    order_a = _order(channel, shipping_code=code)
    order_b = _order(channel, shipping_code="", invoice_code=code)
    verified: list[int] = []

    def _verify(order):
        verified.append(order.id)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        res = client.get(
            _URL, {"code": code, "mode": "dispatch", "invoice_match": "1"}
        )
    assert res.status_code == 200
    body = res.json()
    assert body["multiple"] is True
    assert body["orders"][0]["order_id"] == order_a.id
    assert body["orders"][0]["matched_by"] == "shipping"
    by_id = {o["order_id"]: o["matched_by"] for o in body["orders"]}
    assert by_id[order_b.id] == "invoice"
    assert verified == [order_a.id]  # chỉ verify đơn thắng (mã vận đơn), không phải B


def test_return_mode_does_not_match_invoice_code(client, channel):
    order = _order(channel, shipping_code="", invoice_code="HD-RETURN-001")
    resp = client.get(_URL, {"code": "HD-RETURN-001", "mode": "return"})
    assert resp.status_code == 404
    # đảm bảo không vô tình trả nhầm đơn khác
    assert order.invoice_code == "HD-RETURN-001"


def test_return_mode_does_not_match_invoice_code_even_with_param(client, channel):
    """invoice_match=1 chỉ mở nhánh invoice ở mode=dispatch — return vẫn 404 dù có cờ."""
    order = _order(channel, shipping_code="", invoice_code="HD-RETURN-001B")
    resp = client.get(
        _URL, {"code": "HD-RETURN-001B", "mode": "return", "invoice_match": "1"}
    )
    assert resp.status_code == 404
    assert order.invoice_code == "HD-RETURN-001B"


@pytest.mark.parametrize("extra", [{}, {"invoice_match": "1"}], ids=["no_param", "invoice_match_param"])
def test_return_scan_mode_does_not_match_invoice_code(client, channel, extra):
    """return_scan không khớp mã hoá đơn, kể cả khi client gửi invoice_match=1."""
    _order(channel, shipping_code="", invoice_code="HD-RETURN-002")
    resp = client.get(_URL, {"code": "HD-RETURN-002", "mode": "return_scan", **extra})
    assert resp.status_code == 404




def test_invoice_matched_order_without_warehouse_still_returned(client, channel):
    """Đơn khớp mã hoá đơn nhưng thiếu kho xuất → vẫn trả đơn (FE tự chặn)."""
    order = _order(channel, shipping_code="", invoice_code="HD-NOWH-001")
    assert order.fulfillment_warehouse_id is None
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(
            _URL, {"code": "HD-NOWH-001", "mode": "dispatch", "invoice_match": "1"}
        )
    assert res.status_code == 200
    body = res.json()
    assert body["order_id"] == order.id
    assert body["matched_by"] == "invoice"
    assert body["fulfillment_warehouse_id"] is None


def test_reissued_invoice_code_is_not_matched_by_prefix(client, channel):
    """Equality only: quét HD001 không được vô tình khớp hoá đơn HD001.01."""
    _order(channel, shipping_code="", invoice_code="HD001.01")
    resp = client.get(
        _URL, {"code": "HD001", "mode": "dispatch", "invoice_match": "1"}
    )
    assert resp.status_code == 404


def test_shipping_matched_order_reports_matched_by_shipping(client, channel):
    order = _order(channel)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 200
    assert res.json()["matched_by"] == "shipping"


def test_recall_matched_order_reports_matched_by_recall(client, channel):
    order = _order(channel)
    OrderRecallEntry.objects.create(order=order, recall_code="RC-MATCHEDBY-1")
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": "RC-MATCHEDBY-1", "mode": "dispatch"})
    assert res.status_code == 200
    assert res.json()["matched_by"] == "recall"


@pytest.mark.parametrize("mode", ["return", "Return"])
def test_return_mode_never_verifies_and_allows_cancelled_order(client, channel, mode):
    """Quét nhập hoàn KHÔNG verify huỷ; mode viết hoa vẫn là return, không rơi về dispatch."""
    order = _order(channel, status=OrderStatus.CANCELLED)
    with patch(_VERIFY) as verify:
        res = client.get(_URL, {"code": order.shipping_code, "mode": mode})
    verify.assert_not_called()
    assert res.status_code == 200
    assert res.json()["order_id"] == order.id


def test_verify_does_not_lazy_load_kiotviet_channel(client, channel, django_assert_num_queries):
    """N+1 guard: queryset phải select_related('kiotviet_channel') (verifier đọc field này)."""
    order = _order(channel, kiotviet_channel=KiotVietChannel.objects.create(retailer="R-scanverify"))
    verified: list[int] = []

    def _verify(o: Order) -> CancelCheck:
        with django_assert_num_queries(0):
            assert o.kiotviet_channel is not None
        verified.append(o.id)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        res = client.get(_URL, {"code": order.shipping_code})
    assert res.status_code == 200
    assert verified == [order.id]


def test_payload_exposes_invoice_status(client, channel):
    order = _order(channel, invoice_status=1)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.json()["invoice_status"] == 1


class ShippingLookupDispatchedFlagTests(TestCase):
    """Lookup mã vận đơn phải báo cờ already_dispatched để phần quét kho chặn trùng."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-lookup-dispatched")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lp-lookup-disp", name="Lookup Dispatched")
        self.customer = Customer.objects.create(
            full_name="Khach Lookup",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )
        self.warehouse = Warehouse.objects.create(code="WH-LOOKUP", name="Kho Lookup")
        self.product = Product.objects.create(name="SP Lookup", sku="SKU-LOOKUP")

    def _make_order(self, shipping_code: str) -> Order:
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Lookup",
            phone="0909111222",
            shipping_code=shipping_code,
            total_amount="100000",
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=1,
            unit_price="100000",
            line_total="100000",
        )
        return order

    def _make_order_without_items(self, shipping_code: str) -> Order:
        """Đơn không khớp được sản phẩm nào → không có OrderItem, không có bút toán tồn."""
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Lookup",
            phone="0909111222",
            shipping_code=shipping_code,
            total_amount="100000",
        )

    def _mark_dispatched(self, order: Order) -> None:
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=-1,
            movement_type=MovementType.SALE,
            reference_type="order",
            reference_id=order.id,
        )

    def _mark_undispatched(self, order: Order) -> None:
        """SALE_REVERSAL bù hết SALE (net = 0) — mô phỏng huỷ/un-dispatch."""
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=1,
            movement_type=MovementType.SALE_REVERSAL,
            reference_type="order",
            reference_id=order.id,
        )

    def test_not_dispatched_returns_false(self):
        self._make_order("VD-NEW")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-NEW")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["already_dispatched"])

    def test_dispatched_returns_true(self):
        order = self._make_order("VD-DONE")
        self._mark_dispatched(order)
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-DONE")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["already_dispatched"])

    def test_multiple_branch_flags_only_dispatched_order(self):
        o1 = self._make_order("VD-MULTI")
        self._make_order("VD-MULTI")  # cùng mã vận đơn
        self._mark_dispatched(o1)
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-MULTI")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["multiple"])
        flags = {o["order_id"]: o["already_dispatched"] for o in body["orders"]}
        self.assertTrue(flags[o1.id])
        self.assertFalse(flags[[oid for oid in flags if oid != o1.id][0]])

    def test_lookup_matches_recall_code(self):
        order = self._make_order("VD-HAS-RECALL")
        # recall_code giờ nằm trên OrderRecallEntry (1-N, Task 1) — mỗi nhóm một mã.
        OrderRecallEntry.objects.create(order=order, recall_code="RC-SCAN-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=RC-SCAN-1")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["order_id"], order.id)

    def test_lookup_matches_second_recall_code(self):
        """Mã của nhóm thứ 2 cũng phải tra ra đơn — không chỉ nhóm đầu."""
        order = self._make_order("VD-MULTI-RECALL")
        OrderRecallEntry.objects.create(order=order, recall_code="RC-A")
        OrderRecallEntry.objects.create(order=order, recall_code="RC-B")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=RC-B")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["order_id"], order.id)

    def test_lookup_shipping_code_still_works(self):
        order = self._make_order("VD-PLAIN-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-PLAIN-1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["order_id"], order.id)

    def test_undispatched_order_returns_false(self):
        """SALE + SALE_REVERSAL (net = 0) không còn được coi là đã xuất kho — quét
        lại phải cho phép, không bị bỏ qua verify KiotViet vì tưởng đã xuất rồi."""
        order = self._make_order("VD-UNDISPATCHED")
        self._mark_dispatched(order)
        self._mark_undispatched(order)
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-UNDISPATCHED")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["already_dispatched"])

    def test_dispatched_without_stock_ledger_returns_true(self):
        """Đơn đã ở trạng thái nghiệp vụ "Đã xuất kho" nhưng KHÔNG có bút toán tồn.

        Đơn không khớp được sản phẩm nào (`create_order_manual` chỉ tạo OrderItem khi
        `matched_items`) → `issue_for_order` chạy với `lines=[]`, không sinh movement
        nào. Cờ chỉ đọc sổ tồn sẽ báo False vĩnh viễn → kho quét lại được, không có
        cảnh báo nào, mà bấm Xác nhận cũng không ghi thêm gì (delivery code không đổi).
        """
        order = self._make_order_without_items("VD-NO-ITEMS")
        OrderAssessment.objects.create(order=order, delivery_business_status_code="dispatched")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-NO-ITEMS")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["already_dispatched"])

    def test_dispatched_state_after_stock_moved_to_sibling_returns_true(self):
        """Sổ xuất bị chuyển/hoàn nhưng đơn vẫn đang "Đã xuất kho" → vẫn phải chặn."""
        order = self._make_order("VD-LEDGER-GONE")
        OrderAssessment.objects.create(order=order, delivery_business_status_code="dispatched")
        self._mark_dispatched(order)
        self._mark_undispatched(order)  # net = 0, nhưng trạng thái đơn vẫn "dispatched"
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-LEDGER-GONE")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["already_dispatched"])

    def test_un_dispatched_state_returns_false(self):
        """Kho bỏ trạng thái "Đã xuất kho" (quét nhầm) → phải cho quét lại."""
        order = self._make_order("VD-UNDO-STATE")
        OrderAssessment.objects.create(
            order=order, delivery_business_status_code="warehouse_processing"
        )
        self._mark_dispatched(order)
        self._mark_undispatched(order)
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-UNDO-STATE")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["already_dispatched"])

    def test_multiple_branch_flags_dispatched_state_per_order(self):
        o1 = self._make_order_without_items("VD-MULTI-STATE")
        o2 = self._make_order_without_items("VD-MULTI-STATE")
        OrderAssessment.objects.create(order=o1, delivery_business_status_code="dispatched")
        OrderAssessment.objects.create(
            order=o2, delivery_business_status_code="warehouse_processing"
        )
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-MULTI-STATE")
        self.assertEqual(resp.status_code, 200)
        flags = {o["order_id"]: o["already_dispatched"] for o in resp.json()["orders"]}
        self.assertTrue(flags[o1.id])
        self.assertFalse(flags[o2.id])

    def test_lookup_code_matching_both_fields_returns_multiple(self):
        # Một đơn có shipping_code=X, đơn khác có recall_code=X → nhánh multiple
        order_a = self._make_order("CODE-DUP-1")
        order_b = self._make_order("VD-OTHER-9")
        OrderRecallEntry.objects.create(order=order_b, recall_code="CODE-DUP-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=CODE-DUP-1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("multiple"))
        self.assertEqual(
            {o["order_id"] for o in resp.data["orders"]}, {order_a.id, order_b.id}
        )


# ---------------------------------------------------------------------------
# Mã chưa khớp đơn nào → 404 kèm pending_scan (nếu đã có PendingShipmentScan).
# (Gộp từ test_shipping_lookup_pending.py.)
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho test", code="SLP01")


@pytest.fixture
def api_client():
    user = make_user_with_sections("shiplookup_staff", ["shipments"])
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_lookup_not_found_plain(api_client):
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=NOPE")
    assert resp.status_code == 404
    assert "pending_scan" not in resp.data


def test_lookup_not_found_with_pending_scan(api_client, warehouse):
    PendingShipmentScan.objects.create(
        code="WAIT1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=WAIT1")
    assert resp.status_code == 404
    assert resp.data["pending_scan"]["kind"] == "dispatch"


def test_lookup_not_found_reports_matched_return_scan(api_client, warehouse):
    """Mã đã bắn nhập hoàn xong (scan `matched`) mà đơn nay không tra được nữa.

    Trước bản vá chỉ báo scan còn `pending` → mã đã khớp rồi vẫn im lặng cho bắn
    lại, sinh dòng thứ hai.
    """
    PendingShipmentScan.objects.create(
        code="WAIT2", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status="matched",
    )
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=WAIT2")
    assert resp.status_code == 404
    assert resp.data["pending_scan"]["kind"] == "return"


def test_lookup_not_found_ignores_voided_return_scan(api_client, warehouse):
    """Đã huỷ lần bắn trước → mã tự do, không báo trùng nữa."""
    PendingShipmentScan.objects.create(
        code="WAIT3", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status="matched", voided_at=timezone.now(),
    )
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=WAIT3")
    assert resp.status_code == 404
    assert "pending_scan" not in resp.data


# ---------------------------------------------------------------------------
# Quét NHẬP HOÀN: 1 mã chỉ được bắn 1 lần. Bắn lần 2 bị chặn cứng ngay tại đây
# (cửa đầu tiên người dùng chạm vào) thay vì để lọt tới lúc bấm Xác nhận.
# ---------------------------------------------------------------------------


def _posted_return_entry(warehouse, code: str) -> ReturnEntry:
    note = ReturnNote.objects.create(
        code=f"PTH-{code}", warehouse=warehouse, doc_date=timezone.localdate()
    )
    return ReturnEntry.objects.create(
        return_note=note, shipping_code=code, status=ReturnStatus.POSTED
    )


def test_return_scan_blocks_code_already_returned(api_client, warehouse, channel):
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    _posted_return_entry(warehouse, order.shipping_code)
    resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})
    assert resp.status_code == 409
    assert resp.data["detail"] == "return_already_scanned"
    assert resp.data["note_code"] == f"PTH-{order.shipping_code}"


def test_return_scan_blocks_code_with_live_scan(api_client, warehouse, channel):
    """Mã đã bắn nhưng chưa ra nhóm đơn hoàn (đang chờ khớp) — vẫn là 1 lần bắn sống."""
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    PendingShipmentScan.objects.create(
        code=order.shipping_code, kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})
    assert resp.status_code == 409
    assert resp.data["detail"] == "return_already_scanned"
    assert resp.data["note_code"] == ""


def test_return_scan_ignores_empty_draft_entry(api_client, warehouse, channel):
    """Nháp RỖNG không giữ mã — nếu không sẽ tái hiện bug 'mã bị khoá chết'."""
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    entry = _posted_return_entry(warehouse, order.shipping_code)
    entry.status = ReturnStatus.DRAFT
    entry.save(update_fields=["status"])
    resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})
    assert resp.status_code == 200


def test_return_scan_allows_after_entry_cancelled(api_client, warehouse, channel):
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    entry = _posted_return_entry(warehouse, order.shipping_code)
    entry.status = ReturnStatus.CANCELLED
    entry.save(update_fields=["status"])
    resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})
    assert resp.status_code == 200
    assert resp.data["order_id"] == order.id


def test_plain_return_mode_not_blocked_by_return_history(api_client, warehouse, channel):
    """mode=return thuần chỉ nghĩa là 'không hỏi KiotViet'.

    Màn Tạo phiếu kho thủ công dùng mode này để KÉO DÒNG HÀNG của đơn vào phiếu —
    không tạo lần nhập hoàn nào. Chặn ở đó là hỏng một chức năng không liên quan,
    lại còn báo sai ("Không tìm thấy đơn") vì dialog nuốt mọi lỗi vào một câu.
    """
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    _posted_return_entry(warehouse, order.shipping_code)
    with patch(_VERIFY) as verify:
        resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "return"})
    verify.assert_not_called()
    assert resp.status_code == 200
    assert resp.data["order_id"] == order.id


def test_dispatch_mode_not_blocked_by_return_history(api_client, warehouse, channel):
    """Hàng đi rồi quay về: cùng mã vận đơn phải quét XUẤT được dù đã có lần hoàn."""
    order = _order(channel, status=OrderStatus.IN_DELIVERY)
    _posted_return_entry(warehouse, order.shipping_code)
    with patch(_VERIFY, return_value=CancelCheck.OK):
        resp = api_client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Nở SKU combo ra mã lẻ cho luồng NHẬP HOÀN.
#
# Bán 1 combo trừ tồn theo MÃ LẺ (`expand_desired_quantities` bên trong
# `issue_for_order`), nên hạn mức phiếu hoàn (`_guard_double_credit`, đọc
# `outstanding_by_key`) cũng tính theo mã lẻ. Trả dòng combo về đây thì kho quét
# đúng thứ UI gợi ý vẫn bị chặn "đơn nguồn chỉ còn 0 đơn vị" — đúng lỗi gặp trên
# prod ngày 07/09 với đơn DHTTS_585690038434628865 (combo DC30+NC01).
# `flow_order_lookup` đã nở từ 20/08; đây là CỬA CÒN LẠI phục vụ màn quét.
# ---------------------------------------------------------------------------


def _combo(sku: str, components: list[tuple[Product, int]]) -> Product:
    from apps.catalog.models import ProductComponent

    parent = Product.objects.create(name=f"Combo {sku}", sku=sku)
    for component, qty in components:
        ProductComponent.objects.create(parent=parent, component=component, quantity=qty)
    return parent


def _add_item(order: Order, product: Product, quantity: int) -> None:
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=quantity,
        unit_price="1000",
        line_total="1000",
    )


def test_return_scan_expands_combo_into_components(client, channel):
    dc30 = Product.objects.create(name="Bồn ngâm chân DC30", sku="CB-DC30")
    nc01 = Product.objects.create(name="Thảo dược NC01", sku="CB-NC01")
    combo = _combo("CB-DC30+NC01", [(dc30, 1), (nc01, 1)])
    order = _order(channel)
    _add_item(order, combo, 1)

    res = client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})

    items = {i["product_id"]: i for i in res.json()["items"]}
    assert set(items) == {dc30.id, nc01.id}
    assert items[dc30.id]["sku"] == "CB-DC30"
    assert items[dc30.id]["quantity"] == 1
    assert items[nc01.id]["quantity"] == 1


def test_return_scan_multiplies_component_quantity_by_combo_count(client, channel):
    part = Product.objects.create(name="Thảo dược", sku="CB-PART")
    combo = _combo("CB-PACK3", [(part, 3)])
    order = _order(channel)
    _add_item(order, combo, 2)

    res = client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})

    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["product_id"] == part.id
    assert items[0]["quantity"] == 6


def test_return_scan_merges_combo_component_with_standalone_line(client, channel):
    """Đơn vừa mua combo chứa mã A vừa mua A lẻ → MỘT dòng gộp.

    Đường ghi sổ gộp hai phần thành một bút toán (`expand_desired_quantities`), nên
    `outstanding_by_key` chỉ có một key; trả về hai dòng thì `build_lines` chặn
    "trùng sản phẩm/loại hoàn" và kho không ghi sổ được.
    """
    part = Product.objects.create(name="Thảo dược", sku="CB-MERGE")
    combo = _combo("CB-MERGE-COMBO", [(part, 1)])
    order = _order(channel)
    _add_item(order, combo, 1)
    _add_item(order, part, 2)

    res = client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})

    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["product_id"] == part.id
    assert items[0]["quantity"] == 3


def test_return_mode_expands_combo_too(client, channel):
    """Màn phiếu kho thủ công (`mode=return`) kéo dòng hàng của đơn vào phiếu hoàn
    qua đúng endpoint này → cùng phải nở, nếu không vẫn dính hạn mức mã lẻ."""
    part = Product.objects.create(name="Thảo dược", sku="CB-RET")
    combo = _combo("CB-RET-COMBO", [(part, 1)])
    order = _order(channel)
    _add_item(order, combo, 1)

    res = client.get(_URL, {"code": order.shipping_code, "mode": "return"})

    assert [i["product_id"] for i in res.json()["items"]] == [part.id]


def test_return_scan_keeps_combo_without_declared_components(client, channel):
    """Combo CHƯA khai thành phần giữ nguyên — cùng luật fallback với đường ghi sổ
    (`expand_desired_quantities`), nơi tồn vẫn trừ vào chính SKU combo."""
    combo = Product.objects.create(name="Combo chưa khai", sku="CB-NODECL")
    order = _order(channel)
    _add_item(order, combo, 1)

    res = client.get(_URL, {"code": order.shipping_code, "mode": "return_scan"})

    items = res.json()["items"]
    assert [i["product_id"] for i in items] == [combo.id]
    assert items[0]["sku"] == "CB-NODECL"


def test_dispatch_mode_keeps_combo_line(client, channel):
    """Ranh giới CỐ Ý: quét XUẤT không nở.

    Ở đó `items` chỉ để hiển thị/gộp "tổng hàng cần lấy"; lines xuất kho do
    `issue_for_order` tự dựng từ `OrderItem` rồi nở bên trong. Nở ở đây chỉ đổi
    thứ nhân viên nhặt hàng nhìn thấy mà không sửa lỗi nào.
    """
    part = Product.objects.create(name="Thảo dược", sku="CB-DISP")
    combo = _combo("CB-DISP-COMBO", [(part, 1)])
    order = _order(channel)
    _add_item(order, combo, 1)

    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})

    assert [i["product_id"] for i in res.json()["items"]] == [combo.id]
