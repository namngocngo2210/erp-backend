"""Ghi chú tự do cho mọi dòng của trang Lịch sử nhập xuất.

Bao trọn một luồng: bảng lưu (`stock_flow_notes`) → service điều hướng theo
`target_type` → endpoint PATCH → note hiện trong 3 danh sách của trang.
Thiết kế: docs/flow-history-notes.md
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.catalog.models import Product
from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)
from apps.inventory.application.services.flow_note_service import FlowNoteService
from apps.inventory.application.services.issue_document_history import (
    MergedIssueHistory,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    CancelledDispatchWatch,
    DocType,
    MovementType,
    PendingScanKind,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    StockDocument,
    StockFlowNote,
    StockFlowNoteTarget,
)
from apps.orders.models import Order
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse


class StockFlowNoteModelTests(TestCase):
    def test_unique_per_target(self):
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=1, note="a"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockFlowNote.objects.create(
                target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=1, note="b"
            )

    def test_same_id_different_type_coexist(self):
        """Cùng một đơn có 2 ghi chú độc lập: một ở tab Xuất, một ở tab Chờ hoàn."""
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=7, note="xuất"
        )
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.AWAITING_RETURN, target_id=7, note="chờ hoàn"
        )
        self.assertEqual(StockFlowNote.objects.filter(target_id=7).count(), 2)


class FlowNoteServiceTests(TestCase):
    """Điều hướng theo `target_type`: 2 loại vào bảng mới, 3 loại vào field sẵn có."""

    def test_order_dispatch_upsert_overwrites(self):
        FlowNoteService.update(
            target_type="order_dispatch", target_id=9, note="lần 1", user_id=None
        )
        FlowNoteService.update(
            target_type="order_dispatch", target_id=9, note="lần 2", user_id=None
        )
        rows = StockFlowNote.objects.filter(target_type="order_dispatch", target_id=9)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().note, "lần 2")

    def test_awaiting_return_does_not_touch_order_dispatch(self):
        FlowNoteService.update(
            target_type="order_dispatch", target_id=5, note="đóng thiếu 1", user_id=None
        )
        FlowNoteService.update(
            target_type="awaiting_return", target_id=5, note="khách hẹn 20/8", user_id=None
        )
        self.assertEqual(
            FlowNoteService.notes_for("order_dispatch", [5]), {5: "đóng thiếu 1"}
        )
        self.assertEqual(
            FlowNoteService.notes_for("awaiting_return", [5]), {5: "khách hẹn 20/8"}
        )

    def test_stock_document_writes_document_field(self):
        doc = StockDocument.objects.create(code="PX-FN-1", doc_type=DocType.ISSUE)
        FlowNoteService.update(
            target_type="stock_document", target_id=doc.id, note="phiếu bù đơn X",
            user_id=None,
        )
        doc.refresh_from_db()
        self.assertEqual(doc.note, "phiếu bù đơn X")
        self.assertEqual(StockFlowNote.objects.count(), 0)

    def test_pending_scan_writes_scan_field(self):
        warehouse = Warehouse.objects.create(code="WH-FN", name="Kho FN")
        scan = PendingShipmentScan.objects.create(
            code="VD-FN-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
        )
        FlowNoteService.update(
            target_type="pending_scan", target_id=scan.id, note="quét nhầm", user_id=None
        )
        scan.refresh_from_db()
        self.assertEqual(scan.note, "quét nhầm")

    def test_return_entry_writes_entry_field(self):
        warehouse = Warehouse.objects.create(code="WH-FN2", name="Kho FN2")
        day_note = ReturnNote.objects.create(
            code="PTH-FN-1", warehouse=warehouse, doc_date=timezone.localdate()
        )
        entry = ReturnEntry.objects.create(return_note=day_note)
        FlowNoteService.update(
            target_type="return_entry", target_id=entry.id, note="hàng vỡ", user_id=None
        )
        entry.refresh_from_db()
        self.assertEqual(entry.note, "hàng vỡ")

    def test_unknown_target_type(self):
        with self.assertRaises(ValueError):
            FlowNoteService.update(
                target_type="linh tinh", target_id=1, note="x", user_id=None
            )

    def test_missing_entity_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            FlowNoteService.update(
                target_type="stock_document", target_id=999999, note="x", user_id=None
            )

    def test_notes_for_empty_ids_returns_empty(self):
        self.assertEqual(FlowNoteService.notes_for("order_dispatch", []), {})


class FlowNoteApiTests(TestCase):
    """PATCH /api/inventory/flow-notes — một cửa cho mọi loại dòng."""

    URL = "/api/inventory/flow-notes"

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("flow-note-admin")

    def _patch(self, payload, user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.patch(self.URL, payload, format="json")

    def test_writes_order_dispatch_note(self):
        res = self._patch(
            {"target_type": "order_dispatch", "target_id": 12, "note": "đóng thiếu 1"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["note"], "đóng thiếu 1")
        self.assertEqual(
            FlowNoteService.notes_for("order_dispatch", [12]), {12: "đóng thiếu 1"}
        )

    def test_records_editor(self):
        self._patch({"target_type": "awaiting_return", "target_id": 3, "note": "x"})
        row = StockFlowNote.objects.get(target_type="awaiting_return", target_id=3)
        self.assertEqual(row.updated_by_id, self.admin.id)

    def test_unknown_target_type_is_400(self):
        res = self._patch({"target_type": "linh tinh", "target_id": 1, "note": "x"})
        self.assertEqual(res.status_code, 400)

    def test_non_integer_target_id_is_400(self):
        res = self._patch({"target_type": "order_dispatch", "target_id": "abc", "note": "x"})
        self.assertEqual(res.status_code, 400)

    def test_note_must_be_string(self):
        res = self._patch({"target_type": "order_dispatch", "target_id": 1, "note": 5})
        self.assertEqual(res.status_code, 400)

    def test_missing_entity_is_404(self):
        res = self._patch(
            {"target_type": "stock_document", "target_id": 999999, "note": "x"}
        )
        self.assertEqual(res.status_code, 404)

    def test_requires_stock_edit_permission(self):
        viewer = make_user_with_permissions("flow-note-viewer", ["stock:view"])
        res = self._patch(
            {"target_type": "order_dispatch", "target_id": 1, "note": "x"}, user=viewer
        )
        self.assertEqual(res.status_code, 403)


class FlowNoteInListsTests(TestCase):
    """Note phải đi kèm dòng dữ liệu ở CẢ 3 danh sách của trang.

    Nhồi note ngay trong `*_page` (thay vì ở tầng view) là có chủ ý: job export
    Excel gọi đúng các hàm đó nên file Excel không thể lệch màn hình.
    """

    def setUp(self):
        self.actor = make_admin("flow-note-lists")
        self.warehouse = Warehouse.objects.create(code="WH-FNL", name="Kho FNL")
        self.product = Product.objects.create(sku="SKU-FNL", name="SP FNL")
        self.channel = Channel.objects.create(code="ch-fnl", name="FNL")
        self.customer = Customer.objects.create(
            full_name="Khach FNL", phone_raw="0900000311", phone_normalized="0900000311"
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="DH-FNL",
            invoice_code="HD-FNL",
            shipping_code="VD-FNL",
            fulfillment_warehouse=self.warehouse,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=-4,
            movement_type=MovementType.SALE,
            reference_type="order",
            reference_id=self.order.id,
        )

    def _issue_document(self, note: str) -> StockDocument:
        doc = StockDocumentService.create_document(
            doc_type=DocType.ISSUE,
            source_warehouse_id=self.warehouse.id,
            lines=[{"product_id": self.product.id, "quantity": 2}],
            note=note,
            user_id=self.actor.id,
        )
        StockDocumentService.submit_for_approval(
            document_id=doc.id, user_id=self.actor.id
        )
        StockDocumentService.approve_document(document_id=doc.id, user_id=self.actor.id)
        return doc

    def test_dispatch_rows_carry_note_and_doc_id(self):
        doc = self._issue_document("ghi chú phiếu")
        FlowNoteService.update(
            target_type="order_dispatch",
            target_id=self.order.id,
            note="đóng thiếu 1",
            user_id=self.actor.id,
        )
        items = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})[
            "items"
        ]
        by_kind = {it["kind"]: it for it in items}
        self.assertEqual(by_kind["order"]["note"], "đóng thiếu 1")
        self.assertIsNone(by_kind["order"]["doc_id"])
        self.assertEqual(by_kind["document"]["note"], "ghi chú phiếu")
        self.assertEqual(by_kind["document"]["doc_id"], doc.id)

    def test_dispatch_row_without_note_is_blank(self):
        items = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})[
            "items"
        ]
        self.assertEqual(items[0]["note"], "")

    def test_awaiting_return_row_carries_its_own_note(self):
        """Note tab Chờ hoàn KHÔNG dùng chung với note tab Xuất của cùng đơn."""
        CancelledDispatchWatch.objects.create(
            order=self.order,
            warehouse=self.warehouse,
            cancelled_at=timezone.now(),
        )
        FlowNoteService.update(
            target_type="order_dispatch",
            target_id=self.order.id,
            note="chuyện lúc xuất",
            user_id=self.actor.id,
        )
        FlowNoteService.update(
            target_type="awaiting_return",
            target_id=self.order.id,
            note="khách hẹn trả 20/8",
            user_id=self.actor.id,
        )
        _, rows = AwaitingReturnService.list_page(limit=50, offset=0)
        self.assertEqual(rows[0]["note"], "khách hẹn trả 20/8")
