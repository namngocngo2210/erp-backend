"""Management command của luồng nhập hoàn (call_command + StringIO, dry-run/apply).

Gộp từ test_find_duplicate_return_notes.py, test_post_matched_return_drafts_command.py.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnStatus,
    StockItem,
    StockMovement,
)
from apps.orders.models import Order, OrderStatus
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class FindDuplicateReturnNotesTests(TestCase):
    """Command find_duplicate_return_notes: rà + dọn nhóm đơn hoàn TRÙNG mã vận đơn."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-DUP", name="Kho DUP")
        self.p1 = Product.objects.create(name="SP1", sku="SKU-DUP1")
        self.channel = Channel.objects.create(code="ch-dup", name="DUP Channel")
        self.customer = Customer.objects.create(
            full_name="Khach DUP",
            phone_raw="0912345672",
            phone_normalized="0912345672",
            code="CUST-DUP",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach DUP",
            phone="0912345672",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _qty(self, state="sellable"):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.p1, state=state
        ).first()
        return item.quantity if item else 0

    def _make_posted_entry(self, code: str) -> ReturnEntry:
        """Nhóm đơn POSTED cho 1 mã vận đơn."""
        return ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=code,
        )

    def _make_duplicate_pair(self, code: str) -> tuple[ReturnEntry, ReturnEntry]:
        """Ép 2 nhóm đơn POSTED trùng mã (mô phỏng data lịch sử trước guard):
        tạm xoá shipping_code của nhóm 1 để guard không chặn nhóm 2."""
        e1 = self._make_posted_entry(code)
        ReturnEntry.objects.filter(id=e1.id).update(shipping_code="")
        e2 = self._make_posted_entry(code)
        ReturnEntry.objects.filter(id=e1.id).update(shipping_code=code)
        return e1, e2

    def test_fix_cancels_extra_and_restores_stock(self):
        code = "TRACK-CLEAN-1"
        e1, e2 = self._make_duplicate_pair(code)
        self.assertEqual(self._qty(), 2)  # double count

        out = StringIO()
        call_command("find_duplicate_return_notes", "--fix", stdout=out)

        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.status, ReturnStatus.POSTED)  # giữ nhóm sớm nhất
        self.assertEqual(e2.status, ReturnStatus.CANCELLED)  # huỷ nhóm dư
        self.assertEqual(self._qty(), 1)  # tồn về đúng
        # Huỷ sinh movement đảo chiều (âm) trỏ đúng nhóm đơn dư.
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="return_entry",
                reference_id=e2.id,
                movement_type=MovementType.RETURN_IN,
                qty_delta__lt=0,
            ).exists()
        )
        # Log in ra mã phiếu NGÀY của nhóm dư (entry không còn mã riêng).
        self.assertIn(e2.return_note.code, out.getvalue())

    def test_dry_run_does_not_change_anything(self):
        code = "TRACK-CLEAN-2"
        _, e2 = self._make_duplicate_pair(code)

        out = StringIO()
        call_command("find_duplicate_return_notes", stdout=out)

        e2.refresh_from_db()
        self.assertEqual(e2.status, ReturnStatus.POSTED)  # không đụng
        self.assertEqual(self._qty(), 2)  # vẫn double (chưa fix)


class PostMatchedReturnDraftsCommandTests(TestCase):
    """Backfill command: ghi sổ nhóm đơn hoàn NHÁP đã khớp đơn (delivery_returned,
    có dòng); bỏ qua nhóm chờ khớp 0 dòng và nhóm không có source_order."""

    def setUp(self):
        self.wh = Warehouse.objects.create(code="WBF1", name="Kho Backfill")
        self.product = Product.objects.create(name="SP BF", sku="BF-1")
        self.channel = Channel.objects.create(code="ch-bf", name="BF Channel")
        self.customer = Customer.objects.create(
            full_name="Khach BF",
            phone_raw="0912345673",
            phone_normalized="0912345673",
            code="CUST-BF",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach BF",
            phone="0912345673",
            total_amount="100000",
            fulfillment_warehouse=self.wh,
        )

    def _matched_draft(self):
        return ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
        )

    def test_dry_run_posts_nothing(self):
        entry = self._matched_draft()
        out = StringIO()
        call_command("post_matched_return_drafts", "--dry-run", stdout=out)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ReturnStatus.DRAFT)
        self.assertIn(entry.return_note.code, out.getvalue())

    def test_posts_matched_draft_and_marks_order(self):
        entry = self._matched_draft()
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ReturnStatus.POSTED)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_skips_unmatched_zero_line_draft(self):
        # nhóm chờ khớp: có shipping_code, không source_order, 0 dòng
        pending = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VNBF-PENDING",
        )
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        pending.refresh_from_db()
        self.assertEqual(pending.status, ReturnStatus.DRAFT)  # không đụng tới

    def test_skips_matched_zero_line_draft(self):
        # cô lập điều kiện "có dòng": có source_order nhưng 0 dòng — vẫn phải bị
        # loại dù khớp mọi tiêu chí khác.
        zero_line = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            source_order_id=self.order.id,
        )
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        zero_line.refresh_from_db()
        self.assertEqual(zero_line.status, ReturnStatus.DRAFT)  # không đụng tới

    def test_skips_non_delivery_returned_draft(self):
        # nhóm bảo hành (dòng warranty) không thuộc phạm vi backfill hàng hoàn.
        warranty = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "warranty",
                }
            ],
            source_order_id=self.order.id,
        )
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        warranty.refresh_from_db()
        self.assertEqual(warranty.status, ReturnStatus.DRAFT)
