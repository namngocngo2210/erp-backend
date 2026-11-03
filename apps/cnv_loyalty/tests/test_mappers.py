"""Mapper là pure function — test thẳng, không DB."""

from datetime import datetime, UTC
from decimal import Decimal
from types import SimpleNamespace

from apps.cnv_loyalty.domain import mappers
from apps.orders.models import OrderStatus


class TestSplitVietnameseName:
    def test_full_name(self):
        assert mappers.split_vietnamese_name("Nguyễn Văn An") == ("Nguyễn Văn", "An")

    def test_single_word(self):
        assert mappers.split_vietnamese_name("An") == ("", "An")

    def test_empty(self):
        assert mappers.split_vietnamese_name("") == ("", "Khách")

    def test_none(self):
        assert mappers.split_vietnamese_name(None) == ("", "Khách")

    def test_extra_whitespace(self):
        assert mappers.split_vietnamese_name("  Hồ   Văn  Khoa ") == ("Hồ Văn", "Khoa")


class TestMapCustomer:
    def test_payload(self):
        customer = SimpleNamespace(
            full_name="Trần Thị Bích", phone_normalized="0912345678", phone_raw="+84912345678"
        )
        payload = mappers.map_customer(customer)
        assert payload == {
            "customer": {
                "last_name": "Trần Thị",
                "first_name": "Bích",
                "phone": "0912345678",
            }
        }

    def test_falls_back_to_phone_raw(self):
        customer = SimpleNamespace(full_name="A", phone_normalized="", phone_raw="0909")
        assert payload_phone(mappers.map_customer(customer)) == "0909"


def payload_phone(payload):
    return payload["customer"]["phone"]


class TestMapProduct:
    def test_single_default_variant(self):
        product = SimpleNamespace(
            name="Ly nhựa",
            sku="LN-01",
            price=Decimal("35000.00"),
            weight=Decimal("0.5"),
            created_at=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
        )
        payload = mappers.map_product(product)
        variant = payload["product"]["variants"][0]
        assert payload["product"]["title"] == "Ly nhựa"
        assert variant["sku"] == "LN-01"
        assert variant["price"] == 35000
        assert isinstance(variant["price"], int)
        # Payload phải bám mẫu doc CNV — bản tối giản bị "Data is invalid" (2026-08-06).
        assert payload["product"]["options"][0]["name"] == "Title"
        assert variant["option1"] == "Default Title"
        assert payload["product"]["published_at"]


class TestMapOrderStatus:
    def test_delivered_success_is_paid_fulfilled(self):
        result = mappers.map_order_status(OrderStatus.DELIVERED_SUCCESS)
        assert result["financial_status"] == "paid"
        assert result["fulfillment_status"] == "fulfilled"
        assert result["cancelled_status"] == "uncancelled"

    def test_returned_is_refunded_restocked(self):
        result = mappers.map_order_status(OrderStatus.RETURNED)
        assert result["financial_status"] == "refunded"
        assert result["fulfillment_status"] == "restocked"

    def test_cancelled(self):
        result = mappers.map_order_status(OrderStatus.CANCELLED)
        assert result["cancelled_status"] == "cancelled"
        assert result["financial_status"] == "pending"

    def test_every_status_maps(self):
        # Trạng thái mới thêm vào OrderStatus không được làm mapper nổ —
        # mặc định rơi về pending/uncancelled.
        for status in OrderStatus:
            result = mappers.map_order_status(status)
            assert result["financial_status"] in {"pending", "paid", "refunded"}
            assert result["cancelled_status"] in {"cancelled", "uncancelled"}


def _fake_order(**overrides):
    defaults = {
        "id": 7,
        "order_code": "DH-007",
        "status": OrderStatus.NEW,
        "total_amount": Decimal("200000"),
        "discount_amount": Decimal("20000"),
        "total_after_discount": Decimal("180000"),
        "created_at": datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
        "phone": "0912345678",
        "address": "102 Bà Triệu",
        "raw_address": "",
        "province": "Hà Nội", "province_code": "HN",
        "district": "Hai Bà Trưng", "district_code": "HBT",
        "ward": "Phường Lê Đại Hành", "ward_code": "LDH",
        "name": "Nguyễn Văn An",
        "customer": SimpleNamespace(
            full_name="Nguyễn Văn An",
            phone_normalized="0912345678",
            phone_raw="0912345678",
        ),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_item(product_id=11, price="100000"):
    return SimpleNamespace(
        product_id=product_id,
        product_name="Ly nhựa",
        sku="LN-01",
        quantity=2,
        unit_price=Decimal(price),
    )


class TestMapOrder:
    def test_full_payload(self):
        order = _fake_order()
        payload = mappers.map_order(
            order,
            [_fake_item()],
            customer_cnv_id=555,
            product_cnv_map={11: {"id": 999, "variant_id": 888}},
        )
        body = payload["order"]
        assert body["name"] == "DH-007"
        assert body["unique_id"] == "desi-7"
        assert body["customer"]["id"] == 555
        assert body["customer"]["phone"] == "0912345678"
        assert body["subtotal_price"] == 200000
        assert body["total_discounts"] == 20000
        assert body["total_price"] == 180000
        item = body["line_items"][0]
        assert item["product_id"] == 999
        assert item["variant_id"] == 888
        assert item["product_exists"] is True
        assert item["price"] == 100000
        assert body["shipping_address"]["province_code"] == "HN"

    def test_unmapped_product_still_included(self):
        payload = mappers.map_order(
            _fake_order(), [_fake_item(product_id=12)], customer_cnv_id=1, product_cnv_map={}
        )
        item = payload["order"]["line_items"][0]
        assert item["product_exists"] is False
        assert item["product_id"] == 0

    def test_order_without_code_uses_id(self):
        payload = mappers.map_order(
            _fake_order(order_code=""), [], customer_cnv_id=1, product_cnv_map={}
        )
        assert payload["order"]["name"] == "#7"

    def test_total_derived_when_after_discount_zero(self):
        payload = mappers.map_order(
            _fake_order(total_after_discount=Decimal("0")),
            [], customer_cnv_id=1, product_cnv_map={},
        )
        assert payload["order"]["total_price"] == 180000

    def test_customer_without_cnv_id_still_carries_phone(self):
        """Khách đã tồn tại sẵn phía CNV → không có id, CNV khớp theo SĐT."""
        payload = mappers.map_order(
            _fake_order(), [], customer_cnv_id=None, product_cnv_map={}
        )
        customer = payload["order"]["customer"]
        assert "id" not in customer
        assert customer["phone"] == "0912345678"
        assert (customer["last_name"], customer["first_name"]) == ("Nguyễn Văn", "An")

    def test_no_address_omits_shipping(self):
        payload = mappers.map_order(
            _fake_order(address="", raw_address=""), [], customer_cnv_id=1, product_cnv_map={}
        )
        assert "shipping_address" not in payload["order"]
