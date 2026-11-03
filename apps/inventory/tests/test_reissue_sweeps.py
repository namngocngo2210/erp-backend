"""Sweep vá sổ cho ca "huỷ đơn → xuất lại `<mã>.NN`" (ReissueLedgerRepairService).

Gộp từ test_reissue_ledger_sweep.py và test_reissue_outstanding_sweep.py
(tái cấu trúc test 2026-08-05). Hai file mô phỏng HAI vết legacy KHÁC NHAU:
`_buggy_cancel` (huỷ có auto-release, luật cũ) và `_cancel_without_release`
(huỷ giữ outstanding, luật mới) — cả hai đều được giữ nguyên.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.reissue_ledger_repair_service import (
    ReissueLedgerRepairService,
)
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    CANCEL_RELEASE_NOTE,
    SIBLING_TRANSFER_REISSUE_NOTE,
    SIBLING_TRANSFER_RELEASE_NOTE,
    SaleLine,
    StockSalesService,
    outstanding_by_key,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.inventory.tasks import sweep_reissue_ledger
from apps.inventory.tests.helpers import movements_for
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class _Base(TestCase):
    """Setup kho Late + các helper byte-identical dùng chung cho cả hai cụm sweep."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LATE", name="Kho Late")
        self.product = Product.objects.create(name="SP Late", sku="SKU-LATE")
        self.channel = Channel.objects.create(code="ch-late", name="Late Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Late",
            phone_raw="0912345672",
            phone_normalized="0912345672",
            code="CUST-LATE",
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        self.order = self._make_order(invoice_code="HD300", shipping_code="VDLATE1")
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )

    def _make_order(self, *, invoice_code: str, shipping_code: str, status: int = 3):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Late",
            phone="0912345672",
            order_code="DHLATE",
            invoice_code=invoice_code,
            shipping_code=shipping_code,
            status=status,
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _cancel_without_release(self):
        """Luật mới: huỷ giữ nguyên outstanding, sibling CHƯA tồn tại lúc huỷ."""
        Order.objects.filter(pk=self.order.id).update(status=6)

    def _sellable_qty(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _movements(self, order_id: int):
        return movements_for(order_id)


# ---------------------------------------------------------------------------
# Lưới an toàn cho ca "huỷ đơn → xuất lại" mà sổ xuất bị bỏ rơi.
#
# Sự cố prod 2026-07-21: 10 cặp đơn (12 sản phẩm) có đơn gốc bị huỷ ĐÃ hoàn tồn
# trong khi đơn xuất lại `<mã>.NN` không hề có bút toán nào — hàng đã giao cho
# khách mà sổ vẫn đếm là tồn trong kho. `transfer_issue_to_sibling` chỉ bắt tại
# đúng khoảnh khắc webhook huỷ về; hai luồng KiotViet độc lập đến sai thứ tự là
# trượt. Sweep này quét lại và ghi sổ bù cho đơn xuất lại.
# ---------------------------------------------------------------------------


class ReissueLedgerSweepTests(_Base):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LEAK", name="Kho Leak")
        self.product = Product.objects.create(name="SP Leak", sku="SKU-LEAK")
        self.channel = Channel.objects.create(code="ch-leak", name="Leak Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Leak",
            phone_raw="0912345671",
            phone_normalized="0912345671",
            code="CUST-LEAK",
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        self.order = self._make_order(invoice_code="HD200", shipping_code="VDLEAK1")
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )

    def _make_order(self, *, invoice_code: str, shipping_code: str, status: int = 3):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Leak",
            phone="0912345671",
            order_code="DHLEAK",
            invoice_code=invoice_code,
            shipping_code=shipping_code,
            status=status,
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _buggy_cancel(self):
        """Tái hiện đúng vết prod: đơn huỷ thả tồn, đơn xuất lại không được ghi sổ."""
        Order.objects.filter(pk=self.order.id).update(status=6)
        StockSalesService.release_for_order(
            order_id=self.order.id, note=CANCEL_RELEASE_NOTE
        )

    def test_sweep_writes_missing_issue_ledger_on_reissued_sibling(self):
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()
        qty_while_broken = self._sellable_qty()

        summary = ReissueLedgerRepairService.sweep()

        assert summary["repaired"] == 1
        assert [
            (m.movement_type, m.qty_delta, m.note, m.warehouse_id)
            for m in self._movements(sibling.id)
        ] == [(MovementType.SALE, -3, SIBLING_TRANSFER_REISSUE_NOTE, self.warehouse.id)]
        # Hàng đang ở chỗ khách → tồn phải giảm lại đúng 3 cái vừa bị thả nhầm.
        assert self._sellable_qty() == qty_while_broken - 3

    def test_dry_run_reports_pairs_without_touching_ledger(self):
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()
        qty_while_broken = self._sellable_qty()

        summary = ReissueLedgerRepairService.sweep(dry_run=True)

        assert summary["leaks"] == 1
        assert summary["repaired"] == 0
        assert summary["pairs"] == [
            {
                "cancelled_order_id": self.order.id,
                "sibling_order_id": sibling.id,
                "quantity": 3,
            }
        ]
        assert self._movements(sibling.id) == []
        assert self._sellable_qty() == qty_while_broken

    def test_repeated_sweep_does_not_write_twice(self):
        """Task chạy 30' một lần — lần quét sau tuyệt đối không được trừ tồn lại."""
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()
        ReissueLedgerRepairService.sweep()
        qty_after_first = self._sellable_qty()

        second = ReissueLedgerRepairService.sweep()

        assert second["repaired"] == 0
        assert len(self._movements(sibling.id)) == 1
        assert self._sellable_qty() == qty_after_first

    def test_repair_rechecks_inside_lock_against_racing_worker(self):
        """Hai worker cùng quét ra một cặp rồi mới lần lượt ghi.

        Worker thứ hai cầm kết quả quét đã CŨ — phải tự kiểm lại trong lock,
        không thì trừ tồn hai lần. Đây là lý do check nằm sau `lock_order_stock`
        chứ không phải ở `find_leaks`."""
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()
        leak = ReissueLedgerRepairService.find_leaks()[0]

        assert ReissueLedgerRepairService.repair(leak) is True
        qty_after_first = self._sellable_qty()

        assert ReissueLedgerRepairService.repair(leak) is False
        assert len(self._movements(sibling.id)) == 1
        assert self._sellable_qty() == qty_after_first

    def test_partially_returned_order_only_reissues_what_left_the_warehouse(self):
        """1/3 cái đã quét nhập hoàn TRƯỚC khi huỷ → chỉ 2 cái còn ngoài kho.

        Ghi bù phải mirror đúng phần đơn huỷ đã thả (2), không phải toàn bộ số đã
        xuất (3) — nếu không sẽ trừ tồn cho cái hàng đang nằm trong kho.
        """
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code="VDLEAK1",
        )
        self._buggy_cancel()

        summary = ReissueLedgerRepairService.sweep()

        assert summary["repaired"] == 1
        assert [
            (m.movement_type, m.qty_delta) for m in self._movements(sibling.id)
        ] == [(MovementType.SALE, -2)]
        # Kho đầu 10, xuất 3, hoàn về 1, 2 cái ở chỗ khách → tồn đúng phải là 8.
        assert self._sellable_qty() == 8

    def test_repaired_sale_carries_original_dispatch_date(self):
        """Sổ ghi bù mang ngày hàng rời kho của đơn huỷ, không phải ngày sweep chạy.

        Sweep chạy 30' một lần và có thể vá một cặp lệch từ nhiều ngày trước —
        lấy `now()` thì sản lượng xuất dồn hết vào ngày sweep.
        """
        dispatched_at = timezone.now() - timedelta(days=4)
        StockMovement.objects.filter(
            reference_type="order", reference_id=self.order.id
        ).update(created_at=dispatched_at, occurred_at=dispatched_at)
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()

        assert ReissueLedgerRepairService.sweep()["repaired"] == 1

        moved = self._movements(sibling.id)
        assert [m.occurred_at for m in moved] == [dispatched_at]
        assert moved[0].created_at > dispatched_at

    def test_command_dry_run_lists_pair_without_writing(self):
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()
        out = StringIO()

        call_command("repair_reissue_ledger", "--dry-run", stdout=out)

        printed = out.getvalue()
        assert str(self.order.id) in printed
        assert str(sibling.id) in printed
        assert self._movements(sibling.id) == []

    def test_command_writes_ledger_without_dry_run(self):
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()

        call_command("repair_reissue_ledger", stdout=StringIO())

        assert len(self._movements(sibling.id)) == 1

    def test_task_repairs_and_raises_lark_alert(self):
        """Sổ tự lành nhưng người phải BIẾT — im lặng thì lỗi gốc không ai sửa."""
        sibling = self._make_order(invoice_code="HD200.01", shipping_code="VDLEAK1")
        self._buggy_cancel()

        with patch("apps.inventory.tasks.send_stock_negative_alert") as alert:
            result = sweep_reissue_ledger()

        assert result["repaired"] == 1
        assert len(self._movements(sibling.id)) == 1
        alert.assert_called_once()
        assert str(sibling.id) in alert.call_args[0][0]

    def test_task_stays_silent_when_nothing_to_repair(self):
        with patch("apps.inventory.tasks.send_stock_negative_alert") as alert:
            result = sweep_reissue_ledger()

        assert result["repaired"] == 0
        alert.assert_not_called()


# ---------------------------------------------------------------------------
# Lưới an toàn cho sibling `.NN` về TRỄ dưới luật mới (huỷ KHÔNG auto-release).
#
# Từ 2026-07-29 huỷ đơn giữ nguyên outstanding. Nếu webhook huỷ đến khi sibling
# chưa tồn tại, `transfer_issue_to_sibling` tại-chỗ trượt và không gì chuyển sổ
# nữa: sweep cũ chỉ tìm theo `CANCEL_RELEASE_NOTE` (không còn được ghi), còn quét
# nhập hoàn khớp sibling thì fail-open (sibling chưa có SALE) → 1 kiện hàng có
# thể được cộng tồn 2 lần (1 lần vào sibling, 1 lần vào đơn gốc còn outstanding).
# ---------------------------------------------------------------------------


class OutstandingSiblingSweepTests(_Base):
    def test_sweep_transfers_outstanding_to_late_sibling(self):
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")

        summary = ReissueLedgerRepairService.sweep()

        assert summary["repaired"] == 1
        assert [
            (m.movement_type, m.qty_delta, m.note)
            for m in self._movements(sibling.id)
        ] == [(MovementType.SALE, -3, SIBLING_TRANSFER_REISSUE_NOTE)]
        releases = [
            m
            for m in self._movements(self.order.id)
            if m.note == SIBLING_TRANSFER_RELEASE_NOTE
        ]
        assert [(m.movement_type, m.qty_delta) for m in releases] == [
            (MovementType.SALE_REVERSAL, 3)
        ]
        # Chuyển sổ chỉ đổi đơn đứng tên — tổng tồn không đổi (hàng vẫn ngoài kho).
        assert self._sellable_qty() == 7
        assert outstanding_by_key(self.order.id) == {}

    def test_sweep_is_idempotent_for_outstanding_shape(self):
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")
        ReissueLedgerRepairService.sweep()

        second = ReissueLedgerRepairService.sweep()

        assert second["repaired"] == 0
        assert len(self._movements(sibling.id)) == 1
        assert self._sellable_qty() == 7

    def test_sweep_dry_run_reports_outstanding_pair_without_writing(self):
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")

        summary = ReissueLedgerRepairService.sweep(dry_run=True)

        assert summary["leaks"] == 1
        assert summary["repaired"] == 0
        assert summary["pairs"] == [
            {
                "cancelled_order_id": self.order.id,
                "sibling_order_id": sibling.id,
                "quantity": 3,
            }
        ]
        assert self._movements(sibling.id) == []

    def test_sweep_respects_return_credit(self):
        """1/3 đã về kho trước khi huỷ → chỉ chuyển 2 phần còn ngoài kho."""
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code="VDLATE1",
        )
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")

        summary = ReissueLedgerRepairService.sweep()

        assert summary["repaired"] == 1
        assert [
            (m.movement_type, m.qty_delta) for m in self._movements(sibling.id)
        ] == [(MovementType.SALE, -2)]


class ReturnScanAgainstLateSiblingTests(_Base):
    """Quét nhập hoàn khớp sibling chưa có sổ phải TỰ chuyển sổ, không fail-open."""

    def test_scan_on_ledgerless_sibling_transfers_then_credits_once(self):
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")

        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 3,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=sibling.id,
            shipping_code="VDLATE1-RET",
        )

        # Sổ xuất đã phải chuyển sang sibling ngay trong lượt quét.
        assert [
            (m.movement_type, m.qty_delta, m.note)
            for m in self._movements(sibling.id)
        ] == [(MovementType.SALE, -3, SIBLING_TRANSFER_REISSUE_NOTE)]
        # Đơn gốc không còn nợ — không còn cửa cộng tồn lần 2 qua đơn gốc.
        assert outstanding_by_key(self.order.id) == {}
        # 10 ban đầu − 3 xuất + 3 hoàn = 10.
        assert self._sellable_qty() == 10

        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=[
                    {
                        "product_id": self.product.id,
                        "quantity": 3,
                        "return_type": "delivery_returned",
                    }
                ],
                source_order_id=self.order.id,
                shipping_code="VDLATE1-RET2",
            )
        assert self._sellable_qty() == 10

    def test_scan_exceeding_transferred_outstanding_is_rejected(self):
        self._cancel_without_release()
        sibling = self._make_order(invoice_code="HD300.01", shipping_code="VDLATE1")

        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=[
                    {
                        "product_id": self.product.id,
                        "quantity": 5,
                        "return_type": "delivery_returned",
                    }
                ],
                source_order_id=sibling.id,
                shipping_code="VDLATE1-RET",
            )
        assert self._sellable_qty() == 7
