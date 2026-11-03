"""Đơn đã có mã hoá đơn: khoá khối SẢN PHẨM + TÀI CHÍNH, mọi thứ khác vẫn sửa được.

Một module cho cả luật (xem docs/invoice-code-edit-lock.md). Ba trục phải cùng đúng, sai
một trục là hỏng nghiệp vụ:

1. Field sản phẩm/tài chính đổi giá trị → 403.
2. Gửi lại ĐÚNG giá trị cũ (form đơn gửi full object) → KHÔNG chặn.
3. Mọi field NGOÀI hai khối đó → vẫn 200: khách, địa chỉ, phí vận chuyển,
   kênh/sale/chi nhánh/kho, mã đơn/hoá đơn/vận đơn, trạng thái, CRM, thu hồi, VAT. Đây là
   những thứ thường phải vá sau khi đơn đã lên đường, khoá lại là chặn cả vận hành.

Cộng thêm một trục nữa: guard đặt ở view nên các luồng HỆ THỐNG qua service không bị chặn
(job đẩy KiotViet, job KShip, quét kho trừ tồn).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockMovement
from apps.orders.contracts.kiotviet_cancellation_port import CancelVerdict
from apps.orders.invoice_lock import is_invoice_locked
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.tests.helpers import make_admin, make_user_with_sections
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

_INVOICE_CODE = "HD-LOCK-0001"

# Xuất kho phải hỏi KiotViet xem hoá đơn còn sống — chặn ở PORT phía orders (không
# import app kiotviet vào test, xem .importlinter).
_PORT = "apps.orders.contracts.kiotviet_cancellation_port.kiotviet_cancellation_port"


def _port_says_ok() -> MagicMock:
    port = MagicMock()
    port.verify_not_cancelled.return_value = CancelVerdict.OK
    return port


@pytest.fixture
def channel():
    return Channel.objects.create(code="inv-lock-ch", name="Invoice Lock", owner="Invoice Lock")


@pytest.fixture
def product():
    return Product.objects.create(name="SP Lock", sku="SKU-LOCK-1", price=100000)


@pytest.fixture
def other_product():
    return Product.objects.create(name="SP Lock 2", sku="SKU-LOCK-2", price=50000)


@pytest.fixture
def warehouse(product):
    wh = Warehouse.objects.create(code="WH-LOCK", name="Kho Lock")
    StockCommandService.record_movement(
        warehouse_id=wh.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    return wh


@pytest.fixture
def other_warehouse():
    return Warehouse.objects.create(code="WH-LOCK-2", name="Kho Lock 2")


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=make_admin("inv-lock-admin"))
    return api


@pytest.fixture
def staff_user():
    # KHÔNG dùng make_admin: queryset `staff_id` của serializer loại role hệ thống nên
    # admin sẽ rơi vào 400 (dữ liệu không hợp lệ) trước khi tới guard mã hoá đơn.
    return make_user_with_sections("inv-lock-staff", ["orders"])


def _make_order(channel: Channel, product: Product, warehouse: Warehouse, **kwargs) -> Order:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer.objects.create(
        full_name=f"KH {suffix}",
        phone_raw=f"09{suffix[:8]}",
        phone_normalized=f"09{suffix[:8]}",
        code=f"CUST-LOCK-{suffix}",
    )
    kwargs.setdefault("invoice_code", _INVOICE_CODE)
    kwargs.setdefault("shipping_code", "GHN-LOCK-0001")
    kwargs.setdefault("status", OrderStatus.IN_DELIVERY)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code=f"ORD-{suffix}",
        name="Nguyen Van A",
        phone="0900000001",
        raw_address="1 Ly Thuong Kiet, Ha Noi",
        address="1 Ly Thuong Kiet",
        total_amount="200000",
        total_after_discount="200000",
        shipping_fee="20000",
        payment_method="cod",
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


@pytest.fixture
def locked_order(channel, product, warehouse) -> Order:
    return _make_order(channel, product, warehouse)


def _patch(client: APIClient, order: Order, payload: dict):
    return client.patch(f"/api/orders/{order.id}", payload, format="json")


# ---------------------------------------------------------------------------
# 1. Field sản phẩm / tài chính đổi giá trị → 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        # Khối "Sản phẩm & thông số hàng hoá"
        ({"sku": "SKU-KHAC"}, "sku"),
        ({"quantity": 9}, "quantity"),
        ({"weight": "1.5"}, "weight"),
        ({"dimensions": "10x10x10"}, "dimensions"),
        # Khối "Tài chính"
        ({"total_amount": "999000"}, "total_amount"),
        ({"discount_amount": "10000"}, "discount_amount"),
        ({"booking_voucher_discount": "5000"}, "booking_voucher_discount"),
        ({"other_price": "15000"}, "other_price"),
        ({"customer_prepaid": "50000"}, "customer_prepaid"),
        ({"total_after_discount": "999000"}, "total_after_discount"),
    ],
)
def test_locked_field_is_rejected(client, locked_order, payload, expected_field):
    res = _patch(client, locked_order, payload)

    assert res.status_code == 403
    assert expected_field in res.data["locked_fields"]
    assert "mã hóa đơn" in res.data["detail"]


def test_changing_item_quantity_is_rejected(client, locked_order, product):
    res = _patch(client, locked_order, {"items": [{"product_id": product.id, "quantity": 5}]})

    assert res.status_code == 403
    assert "items" in res.data["locked_fields"]
    locked_order.refresh_from_db()
    assert [item.quantity for item in locked_order.items.all()] == [2]


def test_changing_item_unit_price_is_rejected(client, locked_order, product):
    res = _patch(
        client,
        locked_order,
        {"items": [{"product_id": product.id, "quantity": 2, "unit_price": "123000"}]},
    )

    assert res.status_code == 403
    assert "items" in res.data["locked_fields"]


def test_swapping_product_is_rejected(client, locked_order, other_product):
    res = _patch(client, locked_order, {"items": [{"product_id": other_product.id, "quantity": 2}]})

    assert res.status_code == 403
    assert "items" in res.data["locked_fields"]


def test_adding_item_line_is_rejected(client, locked_order, product, other_product):
    res = _patch(
        client,
        locked_order,
        {
            "items": [
                {"product_id": product.id, "quantity": 2},
                {"product_id": other_product.id, "quantity": 1},
            ]
        },
    )

    assert res.status_code == 403
    assert "items" in res.data["locked_fields"]


def test_editing_products_block_from_ui_is_rejected_as_one_payload(client, locked_order, product):
    """UI gửi items + tổng tiền cùng lúc (BlockProducts.commitItems) → phải báo cả hai."""
    res = _patch(
        client,
        locked_order,
        {
            "items": [{"product_id": product.id, "quantity": 3}],
            "total_amount": "300000",
            "total_after_discount": "300000",
        },
    )

    assert res.status_code == 403
    assert set(res.data["locked_fields"]) == {"items", "total_amount", "total_after_discount"}


def test_response_lists_every_locked_field_at_once(client, locked_order):
    """Người dùng phải thấy TẤT CẢ field bị chặn, không phải sửa xong lại đụng field kế."""
    res = _patch(client, locked_order, {"quantity": 9, "weight": "2", "discount_amount": "1000"})

    assert res.status_code == 403
    assert set(res.data["locked_fields"]) == {"quantity", "weight", "discount_amount"}


# ---------------------------------------------------------------------------
# 2. Gửi lại đúng giá trị cũ → không chặn
# ---------------------------------------------------------------------------


def test_resending_unchanged_values_is_not_blocked(client, locked_order, product):
    """Form đơn gửi lại full object; chặn theo sự có mặt của field sẽ 403 oan."""
    res = _patch(
        client,
        locked_order,
        {
            "name": locked_order.name,
            "phone": locked_order.phone,
            # Cả nhóm KHOÁ cũng gửi lại y nguyên — cách viết số khác nhau ("200000.00",
            # "0.000") phải được coi là KHÔNG đổi, không thì form ăn 403 dù user chỉ sửa
            # ghi chú.
            "items": [{"product_id": product.id, "quantity": 2, "unit_price": "100000"}],
            "sku": locked_order.sku,
            "quantity": locked_order.quantity,
            "weight": "0.000",
            "dimensions": locked_order.dimensions,
            "total_amount": "200000.00",
            "total_after_discount": 200000,
            "discount_amount": "0",
            "customer_prepaid": "0.00",
            "crm": {"internal_note": "Khach hen giao lai chieu nay"},
        },
    )

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.assessment.internal_note == "Khach hen giao lai chieu nay"


def test_items_resent_identically_is_not_blocked(client, locked_order, product):
    res = _patch(client, locked_order, {"items": [{"sku": product.sku, "quantity": 2}]})

    assert res.status_code == 200, res.data


# ---------------------------------------------------------------------------
# 3. Mọi field NGOÀI hai khối đó vẫn sửa được
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        # Khách + địa chỉ: sai địa chỉ/SĐT là ca phải vá gấp khi hãng gọi giao.
        {"name": "Nguyen Van B"},
        {"phone": "0900000002"},
        {"customer_gender": 1},
        {"raw_address": "2 Tran Hung Dao, Ha Noi"},
        {"address": "2 Tran Hung Dao"},
        {"province": "Hà Nội"},
        # Phí vận chuyển nằm ở khối "Vận chuyển", không thuộc khối Tài chính.
        {"shipping_fee": "35000"},
        {"payment_method": "transfer"},
        # Quy thuộc.
        {"order_type": "has_return"},
        {"shipping_form": "two_way"},
        {"branch_id": "99"},
        {"created_at": "2026-08-01T00:00:00Z"},
        # Định danh: mã dán sai phải sửa được, không thì đơn kẹt vĩnh viễn.
        {"order_code": "ORD-KHAC"},
        {"invoice_code": "HD-KHAC"},
        {"invoice_id": 12345},
        {"shipping_code": "GHN-KHAC-0002"},
        # Vận hành / hậu kỳ.
        {"status": OrderStatus.DELIVERED_SUCCESS},
        {"crm": {"internal_note": "Khach doi giao lai"}},
        {"recall": {"return_slip_code": "TH-001"}},
        {"recall_entries": [{"recall_code": "TH-001"}]},
        {"vat": "company"},
        {"is_export_vat": True},
        {"vat_link": "https://hoadon.example.com/abc"},
        {"result": "Giao thanh cong"},
        {"feedback": "Khach hai long"},
        {"customer_return": True},
        {"return_date": "2026-08-20"},
    ],
)
def test_field_outside_locked_blocks_stays_editable(client, locked_order, payload):
    res = _patch(client, locked_order, payload)

    assert res.status_code == 200, res.data


def test_changing_channel_stays_editable(client, locked_order):
    other = Channel.objects.create(code="inv-lock-ch-2", name="Invoice Lock 2")

    res = _patch(client, locked_order, {"channel_code": other.code})

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.channel_id == other.id


def test_changing_staff_stays_editable(client, locked_order, staff_user):
    res = _patch(client, locked_order, {"staff_id": staff_user.id})

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.staff_id == staff_user.id


def test_changing_fulfillment_warehouse_stays_editable(client, locked_order, other_warehouse):
    res = _patch(client, locked_order, {"fulfillment_warehouse": other_warehouse.id})

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.fulfillment_warehouse_id == other_warehouse.id


def test_delivery_business_status_stays_editable_for_warehouse(client, locked_order):
    """Cột duy nhất của trang Vận đơn — khoá nó là chết luồng kho."""
    res = _patch(client, locked_order, {"crm": {"delivery_business_status_code": "received"}})

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.assessment.delivery_business_status_code == "received"


def test_marking_dispatched_stays_editable(client, locked_order):
    """Bấm "Đã xuất kho" trên đơn đã có mã hoá đơn là luồng CHÍNH của kho."""
    with mock_patch(_PORT, return_value=_port_says_ok()):
        res = _patch(client, locked_order, {"crm": {"delivery_business_status_code": "dispatched"}})

    assert res.status_code == 200, res.data
    locked_order.refresh_from_db()
    assert locked_order.assessment.delivery_business_status_code == "dispatched"


# ---------------------------------------------------------------------------
# 4. Đơn chưa có mã hoá đơn: hành vi không đổi
# ---------------------------------------------------------------------------


def test_order_without_invoice_code_is_unaffected(client, channel, product, warehouse):
    order = _make_order(channel, product, warehouse, invoice_code="")

    res = _patch(client, order, {"quantity": 9, "total_amount": "300000"})

    assert res.status_code == 200, res.data
    order.refresh_from_db()
    assert order.quantity == 9


def test_shipping_code_alone_is_not_a_lock(client, channel, product, warehouse):
    """Khoá tính theo MÃ HOÁ ĐƠN, không phải mã vận đơn."""
    order = _make_order(channel, product, warehouse, invoice_code="", shipping_code="GHN-9999")

    assert is_invoice_locked(order) is False
    assert _patch(client, order, {"quantity": 9}).status_code == 200


def test_whitespace_only_invoice_code_is_not_a_lock(client, channel, product, warehouse):
    order = _make_order(channel, product, warehouse, invoice_code="   ")

    assert is_invoice_locked(order) is False
    assert _patch(client, order, {"quantity": 9}).status_code == 200


# ---------------------------------------------------------------------------
# 5. Luồng HỆ THỐNG (qua service) không bị guard của view chặn — chủ ý
# ---------------------------------------------------------------------------


def test_service_layer_can_still_write_locked_fields(locked_order):
    """Đồng bộ KiotViet / job nền ghi qua service, không đi qua view nên không bị chặn."""
    actor = make_admin("inv-lock-system")

    OrderService.update_order_inline(locked_order, {"quantity": 9, "weight": "1.5"}, actor)

    locked_order.refresh_from_db()
    assert locked_order.quantity == 9


def test_dispatch_batch_still_deducts_stock_on_locked_order(locked_order, warehouse):
    """Quét kho xuất hàng cho đơn đã có mã hoá đơn là luồng CHÍNH, không được chặn."""
    actor = make_admin("inv-lock-dispatcher")
    with mock_patch(_PORT, return_value=_port_says_ok()):
        results = OrderService.dispatch_orders_batch(
            order_ids=[locked_order.id], warehouse_id=warehouse.id, actor=actor
        )

    assert results
    assert StockMovement.objects.filter(
        reference_type="order",
        reference_id=locked_order.id,
        movement_type=MovementType.SALE,
    ).exists()
