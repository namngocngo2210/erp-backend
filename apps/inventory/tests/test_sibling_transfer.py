"""Chuyển sổ xuất sang đơn xuất lại `<mã>.NN` khi huỷ đơn + backfill ngày xuất.

Gộp từ test_cancel_transfer_to_sibling.py và test_sibling_dispatch_date_backfill.py
(tái cấu trúc test 2026-08-05). Backfill chạy qua management command
`backfill_sibling_dispatch_date` — lệnh SỐNG, chạy lại được, không phải migration.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.sibling_dispatch_date_backfill import (
    backfill_sibling_dispatch_dates,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SIBLING_TRANSFER_REISSUE_NOTE,
    SIBLING_TRANSFER_RELEASE_NOTE,
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.inventory.subscribers import _on_order_status_changed
from apps.inventory.tests.helpers import movements_for
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

CANCELLED = 6


class _SiblingLedgerBase(TestCase):
    """Helper byte-identical dùng chung cho cả hai cụm test trong module."""

    def _movements(self, order_id: int):
        return movements_for(order_id)


class CancelTransferToSiblingTests(_SiblingLedgerBase):
    """Huỷ đơn khi tồn tại đơn XUẤT LẠI (`<mã>.NN`, cùng mã vận đơn) chưa có sổ.

    Ca prod 2026-07-18: KiotViet huỷ-xuất lại hoá đơn nhưng bản `.01` về TRƯỚC
    (webhook ngược thứ tự) hoặc về qua cron import đêm → không reattach được, tạo
    DÒNG ĐƠN MỚI. Webhook huỷ đến sau hoàn tồn cho đơn cũ, còn "trừ lại khi hồi
    sinh" không bao giờ chạy (đơn hồi sinh là bản ghi khác) → tồn phồng dù hàng
    vẫn ngoài kho. Fix: lúc hoàn tồn do huỷ, nếu có đơn anh em đang sống chưa có
    sổ xuất thì CHUYỂN SỔ sang đơn đó thay vì thả tồn về kho.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-SIB", name="Kho Sib")
        self.product = Product.objects.create(name="SP Sib", sku="SKU-SIB")
        self.channel = Channel.objects.create(code="ch-sib", name="Sib Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Sib",
            phone_raw="0912345670",
            phone_normalized="0912345670",
            code="CUST-SIB",
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        self.order = self._make_order(invoice_code="HD100", shipping_code="VDSIB1")
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )

    def _make_order(self, *, invoice_code: str, shipping_code: str, status: int = 3):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Sib",
            phone="0912345670",
            order_code="DHSIB",
            invoice_code=invoice_code,
            shipping_code=shipping_code,
            status=status,
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _sellable_qty(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _cancel(self):
        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=3, new_status=6)
        )

    def test_cancel_with_live_sibling_transfers_ledger(self):
        sibling = self._make_order(invoice_code="HD100.01", shipping_code="VDSIB1")
        qty_before = self._sellable_qty()

        self._cancel()

        # Đơn cũ: reversal chuyển sổ, net về 0.
        old_moves = self._movements(self.order.id)
        transfer_out = [m for m in old_moves if m.note == SIBLING_TRANSFER_RELEASE_NOTE]
        assert len(transfer_out) == 1
        assert transfer_out[0].qty_delta == 3
        assert sum(m.qty_delta for m in old_moves) == 0
        # Đơn mới: nhận sổ xuất đúng SKU/SL/kho.
        new_moves = self._movements(sibling.id)
        assert [
            (m.movement_type, m.qty_delta, m.note, m.warehouse_id) for m in new_moves
        ] == [
            (
                MovementType.SALE,
                -3,
                SIBLING_TRANSFER_REISSUE_NOTE,
                self.warehouse.id,
            )
        ]
        # Tồn kho KHÔNG đổi — hàng vẫn ngoài kho, chỉ chuyển sổ giữa 2 đơn.
        assert self._sellable_qty() == qty_before

    def test_cancel_without_sibling_keeps_stock_deducted(self):
        """Không có đơn xuất lại → KHÔNG hoàn tồn: hàng vẫn ngoài kho, chờ quét nhập hoàn."""
        qty_before = self._sellable_qty()
        self._cancel()
        old_moves = self._movements(self.order.id)
        assert [
            m.note for m in old_moves if m.movement_type == MovementType.SALE_REVERSAL
        ] == []
        assert self._sellable_qty() == qty_before

    def test_sibling_with_own_ledger_leaves_both_ledgers_alone(self):
        sibling = self._make_order(invoice_code="HD100.01", shipping_code="VDSIB1")
        # Đơn anh em đã tự xuất kho (đã có sổ riêng) → không được chuyển đè.
        StockSalesService.issue_for_order(
            order_id=sibling.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )
        sibling_moves_before = len(self._movements(sibling.id))

        self._cancel()

        assert len(self._movements(sibling.id)) == sibling_moves_before
        old_notes = [
            m.note
            for m in self._movements(self.order.id)
            if m.movement_type == MovementType.SALE_REVERSAL
        ]
        assert old_notes == []

    def test_sibling_invoice_base_mismatch_ignored(self):
        # Trùng mã vận đơn nhưng hoá đơn KHÔNG cùng gốc (ca 2 đơn khác nhau
        # dùng chung mã — dữ liệu bẩn đã gặp trên prod) → không chuyển sổ, và cũng
        # không hoàn tồn: hàng của đơn huỷ vẫn đang ở ngoài kho.
        self._make_order(invoice_code="HD999", shipping_code="VDSIB1")
        qty_before = self._sellable_qty()

        self._cancel()

        old_notes = [
            m.note
            for m in self._movements(self.order.id)
            if m.movement_type == MovementType.SALE_REVERSAL
        ]
        assert old_notes == []
        assert self._sellable_qty() == qty_before

    def _backdate(self, order_id: int, at):
        StockMovement.objects.filter(
            reference_type="order", reference_id=order_id
        ).update(created_at=at, occurred_at=at)

    def test_transferred_sale_carries_original_dispatch_date(self):
        """Sổ chuyển sang đơn `.01` mang NGÀY HÀNG RỜI KHO của đơn gốc.

        Ngày ghi sổ (`created_at`) vẫn là lúc chuyển — đó là dấu vết audit. Nhưng
        tab Xuất lọc và sắp xếp theo `occurred_at`: lấy `now()` thì đơn `.01`
        nhảy sang ngày huỷ/tạo đơn mới dù hàng đã rời kho từ trước (ca prod
        HD013469 → HD013469.01).
        """
        dispatched_at = timezone.now() - timedelta(days=3)
        self._backdate(self.order.id, dispatched_at)
        sibling = self._make_order(invoice_code="HD100.01", shipping_code="VDSIB1")

        self._cancel()

        moved = self._movements(sibling.id)
        assert len(moved) == 1
        assert moved[0].occurred_at == dispatched_at
        assert moved[0].created_at > dispatched_at

    def test_transfer_chain_keeps_first_dispatch_date(self):
        """`.01` bị huỷ tiếp rồi xuất lại `.02` — vẫn là ngày rời kho ĐẦU TIÊN.

        Đơn `.01` không có SALE thật nào để bám vào; nếu không kế thừa ngày của
        chính bút toán chuyển sổ thì mỗi vòng huỷ-xuất lại đẩy ngày đi một nấc.
        """
        dispatched_at = timezone.now() - timedelta(days=5)
        self._backdate(self.order.id, dispatched_at)
        first = self._make_order(invoice_code="HD100.01", shipping_code="VDSIB1")
        self._cancel()
        Order.objects.filter(pk=self.order.id).update(status=6)

        second = self._make_order(invoice_code="HD100.02", shipping_code="VDSIB1")
        _on_order_status_changed(
            OrderStatusChanged(order_id=first.id, old_status=3, new_status=6)
        )

        assert [m.occurred_at for m in self._movements(second.id)] == [dispatched_at]

    def test_transfer_is_idempotent_on_repeated_cancel_event(self):
        sibling = self._make_order(invoice_code="HD100.01", shipping_code="VDSIB1")
        self._cancel()
        self._cancel()  # webhook KiotViet retry cùng sự kiện

        assert len(self._movements(sibling.id)) == 1
        old_moves = self._movements(self.order.id)
        assert sum(m.qty_delta for m in old_moves) == 0


class SiblingDispatchDateBackfillTests(_SiblingLedgerBase):
    """Nắn ngày xuất cho các đơn `<mã>.NN` đã nhận sổ TRƯỚC khi có fix đường ghi.

    Ca prod HD013469 → HD013469.01: bút toán "Nhận sổ xuất từ đơn bị huỷ" mang
    `occurred_at = lúc chuyển sổ`, nên tab Xuất xếp đơn `.01` vào ngày tạo đơn mới.
    Sửa đường ghi chỉ đúng cho dòng mới — các dòng đã ghi phải nắn lại một lần.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-BF", name="Kho BF")
        self.product = Product.objects.create(name="SP BF", sku="SKU-BF")
        self.channel = Channel.objects.create(code="ch-bf", name="BF Channel")
        self.customer = Customer.objects.create(
            full_name="Khach BF",
            phone_raw="0912345672",
            phone_normalized="0912345672",
            code="CUST-BF",
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        self.dispatched_at = timezone.now() - timedelta(days=6)
        self.moved_at = timezone.now() - timedelta(days=2)

    def _make_order(self, *, invoice_code: str, shipping_code: str, status: int = 3):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach BF",
            phone="0912345672",
            order_code="DHBF",
            invoice_code=invoice_code,
            shipping_code=shipping_code,
            status=status,
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _seed_legacy_pair(self, *, base="HD300", shipping_code="VDBF1"):
        """Dựng đúng vết dữ liệu cũ: sổ đã chuyển nhưng mang ngày chuyển sổ."""
        old = self._make_order(invoice_code=base, shipping_code=shipping_code)
        StockSalesService.issue_for_order(
            order_id=old.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )
        StockMovement.objects.filter(
            reference_type="order", reference_id=old.id
        ).update(created_at=self.dispatched_at, occurred_at=self.dispatched_at)
        sibling = self._make_order(
            invoice_code=f"{base}.01", shipping_code=shipping_code
        )
        StockSalesService.transfer_issue_to_sibling(
            order_id=old.id, sibling_order_id=sibling.id
        )
        Order.objects.filter(pk=old.id).update(status=CANCELLED)
        # Đường ghi mới đã đóng ngày đúng — hạ về ngày chuyển sổ để tái hiện dòng cũ.
        StockMovement.objects.filter(
            reference_type="order", reference_id=sibling.id
        ).update(created_at=self.moved_at, occurred_at=self.moved_at)
        return old, sibling

    def _sibling_occurred_at(self, sibling_id: int):
        """Ngày của bút toán NHẬN SỔ.

        Không lấy cả sổ của đơn: đơn `.01` bị huỷ tiếp sẽ có thêm reversal chuyển
        sổ đi, và reversal cố tình mang ngày huỷ chứ không phải ngày rời kho.
        """
        return [
            m.occurred_at
            for m in self._movements(sibling_id)
            if m.note == SIBLING_TRANSFER_REISSUE_NOTE
        ]

    def test_backfill_moves_sibling_ledger_to_real_dispatch_date(self):
        _, sibling = self._seed_legacy_pair()

        summary = backfill_sibling_dispatch_dates()

        assert summary["updated"] == 1
        assert self._sibling_occurred_at(sibling.id) == [self.dispatched_at]

    def test_dry_run_reports_without_writing(self):
        old, sibling = self._seed_legacy_pair()

        summary = backfill_sibling_dispatch_dates(dry_run=True)

        assert summary["updated"] == 1
        assert summary["rows"] == [
            {
                "movement_id": self._movements(sibling.id)[0].id,
                "order_id": sibling.id,
                "source_order_id": old.id,
                "occurred_at": self.moved_at.isoformat(),
                "new_occurred_at": self.dispatched_at.isoformat(),
            }
        ]
        assert self._sibling_occurred_at(sibling.id) == [self.moved_at]

    def test_second_run_is_noop(self):
        _, sibling = self._seed_legacy_pair()
        backfill_sibling_dispatch_dates()

        assert backfill_sibling_dispatch_dates()["updated"] == 0
        assert self._sibling_occurred_at(sibling.id) == [self.dispatched_at]

    def test_never_pushes_a_date_forward(self):
        """Dòng đã đúng (hoặc sớm hơn) không bao giờ bị đẩy muộn lại."""
        _, sibling = self._seed_legacy_pair()
        earlier = self.dispatched_at - timedelta(days=1)
        StockMovement.objects.filter(
            reference_type="order", reference_id=sibling.id
        ).update(occurred_at=earlier)

        assert backfill_sibling_dispatch_dates()["updated"] == 0
        assert self._sibling_occurred_at(sibling.id) == [earlier]

    def test_ignores_movement_without_matching_cancelled_source(self):
        """Trùng mã vận đơn nhưng hoá đơn khác gốc → không suy ra được đơn nguồn."""
        _, sibling = self._seed_legacy_pair()
        Order.objects.filter(pk=sibling.id).update(invoice_code="HD999.01")

        summary = backfill_sibling_dispatch_dates()

        assert summary["updated"] == 0
        assert summary["skipped"] == 1
        assert self._sibling_occurred_at(sibling.id) == [self.moved_at]

    def test_backfills_a_chain_of_reissues(self):
        """`.01` rồi `.02` — cả chuỗi phải về ngày rời kho ĐẦU TIÊN."""
        _, first = self._seed_legacy_pair()
        second = self._make_order(invoice_code="HD300.02", shipping_code="VDBF1")
        StockSalesService.transfer_issue_to_sibling(
            order_id=first.id, sibling_order_id=second.id
        )
        Order.objects.filter(pk=first.id).update(status=CANCELLED)
        later = timezone.now() - timedelta(days=1)
        StockMovement.objects.filter(
            reference_type="order", reference_id=second.id
        ).update(created_at=later, occurred_at=later)

        assert backfill_sibling_dispatch_dates()["updated"] == 2
        assert self._sibling_occurred_at(first.id) == [self.dispatched_at]
        assert self._sibling_occurred_at(second.id) == [self.dispatched_at]

    def test_command_dry_run_then_write(self):
        _, sibling = self._seed_legacy_pair()
        out = StringIO()

        call_command("backfill_sibling_dispatch_date", "--dry-run", stdout=out)
        assert str(sibling.id) in out.getvalue()
        assert self._sibling_occurred_at(sibling.id) == [self.moved_at]

        call_command("backfill_sibling_dispatch_date", stdout=StringIO())
        assert self._sibling_occurred_at(sibling.id) == [self.dispatched_at]
