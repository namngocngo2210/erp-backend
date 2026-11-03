"""Lưới hứng: đơn huỷ đã hồi sinh mà tồn chưa được trừ lại phải bị phát hiện.

Sự cố 2026-07-23 (docs/erp-revive-stock-safety-net.md): 2 đơn lọt suốt nhiều ngày, chỉ lộ
ra khi có người đi truy tay. Detector phải dùng ĐÚNG công thức của fixer
(`reissue_after_cancel_release`) — lệch công thức thì có ngày cảnh báo một đằng, chạy sửa
lại không ghi gì.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.revived_stock_audit_service import (
    RevivedStockAuditService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    CANCEL_RELEASE_NOTE,
    REVIVE_REISSUE_NOTE,
    StockSalesService,
)
from apps.inventory.models import MovementType
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    return Customer.objects.create(
        full_name="KH Audit", phone_raw="0909777888", phone_normalized="0909777888"
    )


@pytest.fixture
def warehouse(make_warehouse, product):
    """Override conftest: kho đã NHẬP SẴN 10 `product` — mọi test ở đây hồi tồn từ đó."""
    wh = make_warehouse(code="WH-REVIVE", name="Kho Revive")
    StockCommandService.record_movement(
        warehouse_id=wh.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    return wh


def _make_order(channel, customer, product, warehouse, *, status=OrderStatus.IN_DELIVERY) -> Order:
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="KH Audit",
        phone="0909777888",
        shipping_code=f"VD-REVIVE-{Order.objects.count() + 1}",
        total_amount="100000",
        status=status,
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price="100000",
        line_total="100000",
    )
    return order


def _dispatch(order: Order, warehouse: Warehouse, product: Product) -> None:
    from apps.inventory.contracts.stock_sales import SaleLine

    StockSalesService.issue_for_order(
        order_id=order.id,
        warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=1)],
    )


def _release_as_cancel(order: Order) -> None:
    StockSalesService.release_for_order(order_id=order.id, note=CANCEL_RELEASE_NOTE)


def test_revived_order_without_reissue_is_found(channel, customer, product, warehouse):
    """Đúng hình dạng 2 đơn prod: xuất → huỷ hoàn tồn → sống lại, không ai trừ lại."""
    order = _make_order(channel, customer, product, warehouse)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)  # đơn bị huỷ, tồn trả về kho
    # Đơn sống lại: status rời CANCELLED (ở đây vốn đã là IN_DELIVERY).

    leaks = RevivedStockAuditService.find_unreissued()

    assert [leak.order_id for leak in leaks] == [order.id]
    assert leaks[0].quantity == 1
    assert leaks[0].shipping_code == order.shipping_code


def test_still_cancelled_order_is_not_reported(channel, customer, product, warehouse):
    """Đơn VẪN đang huỷ → hàng đã về kho, hoàn tồn là đúng. Không phải lỗi."""
    order = _make_order(channel, customer, product, warehouse, status=OrderStatus.CANCELLED)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)

    assert RevivedStockAuditService.find_unreissued() == []


def test_reissued_order_is_not_reported(channel, customer, product, warehouse):
    """Đã được trừ lại → sạch. Đây là điều kiện dừng của cảnh báo."""
    order = _make_order(channel, customer, product, warehouse)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)
    StockSalesService.reissue_after_cancel_release(order_id=order.id)

    assert RevivedStockAuditService.find_unreissued() == []


def test_detector_agrees_with_fixer(channel, customer, product, warehouse):
    """Detector báo bao nhiêu thì fixer ghi bù đúng bấy nhiêu — hai bên không được lệch.

    Cùng một công thức nên số lượng phải khớp tuyệt đối; lệch nghĩa là có ngày cảnh báo
    kêu mà chạy sửa lại không ghi gì (hoặc ngược lại, sửa âm thầm mà không ai được báo).
    """
    from apps.inventory.models import StockMovement

    order = _make_order(channel, customer, product, warehouse)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)

    leaks = RevivedStockAuditService.find_unreissued()
    assert [leak.order_id for leak in leaks] == [order.id]

    StockSalesService.reissue_after_cancel_release(order_id=order.id)

    written = -sum(
        StockMovement.objects.filter(
            reference_type="order",
            reference_id=order.id,
            movement_type=MovementType.SALE,
            note=REVIVE_REISSUE_NOTE,
        ).values_list("qty_delta", flat=True)
    )
    assert written == leaks[0].quantity
    # Bù xong thì detector im ngay lần sau — không kêu lại mãi.
    assert RevivedStockAuditService.find_unreissued() == []


def test_never_dispatched_cancelled_order_is_clean(channel, customer, product, warehouse):
    """Đơn huỷ mà chưa từng xuất kho: không có gì để hoàn, cũng không có gì để trừ lại."""
    order = _make_order(channel, customer, product, warehouse)
    _release_as_cancel(order)  # no-op vì outstanding = 0

    assert RevivedStockAuditService.find_unreissued() == []


def test_soft_deleted_order_is_ignored(channel, customer, product, warehouse):
    from django.utils import timezone

    order = _make_order(channel, customer, product, warehouse)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)
    Order.objects.filter(pk=order.pk).update(deleted_at=timezone.now())

    assert RevivedStockAuditService.find_unreissued() == []


def test_limit_caps_result(channel, customer, product, warehouse):
    for _ in range(3):
        order = _make_order(channel, customer, product, warehouse)
        _dispatch(order, warehouse, product)
        _release_as_cancel(order)

    assert len(RevivedStockAuditService.find_unreissued()) == 3
    assert len(RevivedStockAuditService.find_unreissued(limit=2)) == 2


def test_reclaimed_legacy_order_is_not_reported(channel, customer, product, warehouse):
    """Đơn legacy: auto-release khi huỷ → hàng về, phiếu hoàn `reclaim_auto_release`
    trừ lại → đơn sang RETURNED. Sổ đã cân — detector kêu là báo động GIẢ vĩnh viễn
    (fixer dùng công thức có `AUTO_RELEASE_RECLAIM_NOTE` nên sẽ no-op)."""
    from apps.inventory.application.services.stock_sales_service import (
        AUTO_RELEASE_RECLAIM_NOTE,
    )

    order = _make_order(channel, customer, product, warehouse, status=OrderStatus.RETURNED)
    _dispatch(order, warehouse, product)
    _release_as_cancel(order)
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order",
        reference_id=order.id, note=AUTO_RELEASE_RECLAIM_NOTE,
    )

    assert RevivedStockAuditService.find_unreissued() == []


def test_partial_reissue_still_reported(channel, customer, product, warehouse):
    """Bù thiếu (2 hoàn, 1 bù) vẫn phải kêu — im lúc này là bỏ sót đúng nửa vấn đề."""
    order = _make_order(channel, customer, product, warehouse)
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=2,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=order.id, note=CANCEL_RELEASE_NOTE,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
        note=REVIVE_REISSUE_NOTE,
    )

    leaks = RevivedStockAuditService.find_unreissued()
    assert [(leak.order_id, leak.quantity) for leak in leaks] == [(order.id, 1)]


class TestAuditTask:
    def test_task_alerts_when_leaks_found(self, channel, customer, product, warehouse):
        from apps.inventory.tasks import audit_revived_orders_stock

        order = _make_order(channel, customer, product, warehouse)
        _dispatch(order, warehouse, product)
        _release_as_cancel(order)

        with patch("apps.inventory.tasks.send_stock_negative_alert") as alert:
            summary = audit_revived_orders_stock()

        assert summary["found"] == 1
        alert.assert_called_once()
        assert f"#{order.id}" in alert.call_args.args[0]

    def test_task_is_silent_when_clean(self, channel, customer, product, warehouse):
        """Không có gì lệch → KHÔNG bắn Lark. Nhiễu hằng ngày làm cảnh báo thật bị bỏ qua."""
        from apps.inventory.tasks import audit_revived_orders_stock

        with patch("apps.inventory.tasks.send_stock_negative_alert") as alert:
            summary = audit_revived_orders_stock()

        assert summary["found"] == 0
        alert.assert_not_called()
