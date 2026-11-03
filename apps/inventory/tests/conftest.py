"""Fixture dùng chung cho test app inventory.

Trước đây 47 file test không có conftest: `warehouse` định nghĩa lại 12 lần, `product`
7, `channel` 5, `actor`/`order` 3… mỗi bản lệch một chút và trùng tên nhau (pytest
lấy bản gần nhất — khó đọc). Ở đây:

- `make_warehouse` / `make_product` / `make_channel`: factory có tham số, mã tự tăng
  để một test gọi nhiều lần không đụng unique.
- `warehouse` / `product` / `channel`: một đối tượng mặc định cho test chỉ cần "một
  kho / một SP / một kênh" — KHÔNG được assert vào mã/tên cứng, lấy qua thuộc tính.
- `actor` / `staff_user`: admin (role hệ thống) để ghi phiếu/khớp mã.
- `warehouse_with_two_products`: bộ (kho, SP1, SP2) cho test phiếu trả nhiều dòng.
- `make_order`: đơn AWAITING_SHIPMENT gắn kho fulfillment + 1 OrderItem (+ tồn nhập
  sẵn) — file cần đơn đặc thù override `order` mỏng trên factory này.

File nào cần bản khác nghĩa (vd kho đã có tồn) thì override cùng tên tại chỗ kèm
docstring lý do, và gọi lại factory ở đây thay vì tạo tay.
"""

import itertools

import pytest


@pytest.fixture
def make_warehouse(db):
    from apps.warehouses.models import Warehouse

    counter = itertools.count(1)

    def _make(code=None, name=None, **kwargs):
        seq = next(counter)
        return Warehouse.objects.create(
            code=code or f"WH-T{seq}", name=name or f"Kho test {seq}", **kwargs
        )

    return _make


@pytest.fixture
def warehouse(make_warehouse):
    return make_warehouse(code="WH-TEST", name="Kho test")


@pytest.fixture
def make_product(db):
    from apps.catalog.models import Product

    counter = itertools.count(1)

    def _make(sku=None, name=None, **kwargs):
        seq = next(counter)
        return Product.objects.create(
            sku=sku or f"SKU-T{seq}", name=name or f"SP test {seq}", **kwargs
        )

    return _make


@pytest.fixture
def product(make_product):
    return make_product(sku="SKU-TEST-1", name="SP test")


@pytest.fixture
def make_channel(db):
    from apps.channels_core.models import Channel

    counter = itertools.count(1)

    def _make(code=None, name=None, **kwargs):
        seq = next(counter)
        return Channel.objects.create(
            code=code or f"ch-inv-{seq}", name=name or f"Kenh test {seq}", **kwargs
        )

    return _make


@pytest.fixture
def channel(make_channel):
    return make_channel(code="ch-test", name="Kenh test")


@pytest.fixture
def actor(db):
    """Admin (role hệ thống) — người ghi phiếu/khớp mã trong test kho."""
    from apps.users.tests.helpers import make_admin

    return make_admin("inventory-actor")


@pytest.fixture
def staff_user(db):
    """Admin thứ hai, tách khỏi `actor` cho test cần phân biệt người thao tác."""
    from apps.users.tests.helpers import make_admin

    return make_admin("inventory-staff")


@pytest.fixture
def warehouse_with_two_products(make_warehouse, make_product):
    """(kho, SP1, SP2) cho test phiếu trả/điều chỉnh nhiều dòng."""
    wh = make_warehouse(name="Kho HN", code="HN")
    p1 = make_product(sku="SKU1", name="SP 1")
    p2 = make_product(sku="SKU2", name="SP 2")
    return wh, p1, p2


@pytest.fixture
def make_order(db):
    """Đơn kho tối thiểu: Customer riêng + Order AWAITING_SHIPMENT gắn `warehouse`
    làm kho fulfillment + 1 OrderItem của `product` (+ tồn nhập sẵn `stock_qty`)."""
    from apps.customers.models import Customer
    from apps.inventory.application.services.stock_command_service import StockCommandService
    from apps.inventory.models import MovementType
    from apps.orders.models import Order, OrderItem, OrderStatus

    counter = itertools.count(1)

    def _make(
        *,
        channel,
        warehouse,
        product,
        quantity=1,
        unit_price=100_000,
        stock_qty=0,
        name=None,
        phone=None,
        customer_code=None,
        **order_kwargs,
    ):
        seq = next(counter)
        name = name or f"Khach kho {seq}"
        phone = phone or f"0929{seq:06d}"
        customer = Customer.objects.create(
            full_name=name,
            phone_raw=phone,
            phone_normalized=phone,
            code=customer_code or f"CUST-INV-{seq}",
        )
        data = {
            "channel": channel,
            "customer": customer,
            "name": name,
            "phone": phone,
            "total_amount": "200000",
            "status": OrderStatus.AWAITING_SHIPMENT,
            "fulfillment_warehouse": warehouse,
        }
        data.update(order_kwargs)
        order = Order.objects.create(**data)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=quantity,
            unit_price=unit_price,
            line_total=quantity * unit_price,
        )
        if stock_qty:
            StockCommandService.record_movement(
                warehouse_id=warehouse.id,
                product_id=product.id,
                qty_delta=stock_qty,
                movement_type=MovementType.RECEIPT,
            )
        return order

    return _make
