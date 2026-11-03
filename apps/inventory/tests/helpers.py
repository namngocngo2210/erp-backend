"""Helper dùng chung cho test suite inventory.

Trích xuất từ các hàm trùng lặp giữa các test module trong đợt tái cấu trúc
test (xem docs/test-restructure-2026-08-05.md). Giữ nguyên hành vi gốc.
Lưu ý: module này KHÔNG được import apps.kiotviet (ràng buộc import-linter).
"""
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingShipmentScan,
    ReturnType,
    StockItem,
    StockMovement,
    StockState,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product


def _make_customer(suffix: str) -> Customer:
    return Customer.objects.create(
        full_name=f"Khach Pending {suffix}",
        phone_raw=f"092900000{suffix}",
        phone_normalized=f"092900000{suffix}",
        code=f"CUST-PS-{suffix}",
    )


def _make_product(suffix: str) -> Product:
    return Product.objects.create(name=f"SP Pending {suffix}", sku=f"SKU-PS-{suffix}")


def make_order(channel, warehouse, suffix, shipping_code, qty=2):
    customer = _make_customer(suffix)
    product = _make_product(suffix)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name=f"Khach Pending {suffix}",
        phone=f"092900000{suffix}",
        total_amount="200000",
        status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code=shipping_code,
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=qty,
        unit_price=100000,
        line_total=100000 * qty,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=qty,
        movement_type=MovementType.RECEIPT,
    )
    return order


def sellable(warehouse_id: int, product_id: int) -> int:
    item = StockItem.objects.filter(
        warehouse_id=warehouse_id, product_id=product_id, state=StockState.SELLABLE
    ).first()
    return item.quantity if item else 0


def pending_scan(warehouse, actor, code: str) -> PendingShipmentScan:
    return PendingShipmentScan.objects.create(
        code=code,
        kind=PendingScanKind.DISPATCH,
        warehouse=warehouse,
        scanned_by=actor,
    )


def return_line(product, quantity, return_type=ReturnType.DELIVERY_RETURNED):
    return {
        "product_id": product.id,
        "quantity": quantity,
        "return_type": return_type,
    }


def movements_for(order_id: int):
    return list(
        StockMovement.objects.filter(
            reference_type="order", reference_id=order_id
        ).order_by("id")
    )
