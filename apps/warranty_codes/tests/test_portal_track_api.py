import itertools
from decimal import Decimal

import pytest
from datetime import UTC

pytestmark = pytest.mark.django_db

TRACK_URL = "/api/warranty-portal/track"


@pytest.fixture
def portal_order(order_factory):
    """Mặc định cho portal trên fixture gốc `order_factory` (backend/conftest.py):
    đơn đang giao (status 2), tỉnh + tổng tiền + mã đơn/mã vận đơn tuần tự.
    Portal tra theo số điện thoại nên các đơn cùng `phone` dùng chung một Customer
    (fixture gốc get_or_create theo `phone_normalized`); kênh thì mỗi đơn một kênh
    riêng như mọi app khác — portal không đọc kênh.
    """
    counter = itertools.count(1)

    def _make(
        *, phone="0901234567", status=2, province="Hà Nội", shipping_code=None, deleted=False,
        name="Khách", invoice_code="",
    ):
        seq = next(counter)
        return order_factory(
            prefix="portal", phone=phone, name=name, status=status, province=province,
            total_amount=Decimal("500000"), order_code=f"DH{seq:03d}", invoice_code=invoice_code,
            shipping_code=shipping_code or f"SC{seq:05d}", deleted=deleted,
        )

    return _make


@pytest.fixture
def order_item_factory(db):
    def _make(order, *, sku="BNC02", name="Bồn ngâm chân", quantity=1):
        from apps.orders.models import OrderItem
        from apps.catalog.models import Product

        product = Product.objects.create(name=name, sku=sku)
        return OrderItem.objects.create(
            order=order, product=product, product_name=name, sku=sku, quantity=quantity
        )

    return _make


def test_track_valid_pair_returns_order_and_journey(public_client, portal_order, order_item_factory):
    order = portal_order(
        phone="0901234567", shipping_code="SC00001", name="Nguyễn Văn A", invoice_code="HD0001"
    )
    order_item_factory(order, sku="BNC02", name="Bồn ngâm chân", quantity=2)

    r = public_client.post(
        TRACK_URL, {"shipping_code": "sc00001", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 200, r.data
    body = r.data["order"]
    # Danh sách trường là GIAO KÈO của endpoint public: thêm gì vào đây phải cân
    # nhắc lộ lọt, nên assert khớp TUYỆT ĐỐI chứ không phải subset.
    assert set(body) == {
        "order_code", "invoice_code", "shipping_code", "created_at", "status", "status_label",
        "customer_name", "phone", "province", "items",
    }
    assert body["shipping_code"] == "SC00001"
    assert body["invoice_code"] == "HD0001"
    assert body["customer_name"] == "Nguyễn Văn A"
    assert body["phone"] == "0901234567"
    assert body["status_label"] == "Đang giao hàng"
    assert body["items"] == [{"sku": "BNC02", "name": "Bồn ngâm chân", "quantity": 2}]
    assert "journey" in r.data


def test_track_returns_empty_strings_when_optional_fields_missing(public_client, portal_order):
    """Đơn chưa lên hoá đơn/không có tên khách vẫn trả đủ key, giá trị rỗng."""
    portal_order(phone="0901234567", shipping_code="SC00009", name="", invoice_code="")

    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00009", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 200, r.data
    assert r.data["order"]["invoice_code"] == ""
    assert r.data["order"]["customer_name"] == ""


def test_track_does_not_leak_internal_order_fields(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC00010")

    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00010", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 200, r.data
    body = r.data["order"]
    for leaked in ("id", "total_amount", "address", "raw_address", "staff", "note"):
        assert leaked not in body


def test_track_accepts_country_code_phone(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC00002")
    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00002", "phone": "+84 901 234 567"}, format="json"
    )
    assert r.status_code == 200, r.data


@pytest.mark.parametrize(
    "payload",
    [
        {"shipping_code": "SC00003", "phone": "0909999999"},
        {"shipping_code": "KHONGTONTAI", "phone": "0901234567"},
    ],
    ids=["wrong_phone", "wrong_shipping_code"],
)
def test_track_mismatch_returns_404(public_client, portal_order, payload):
    """Sai SĐT hoặc sai mã vận đơn đều là not_found — không tiết lộ vế nào sai."""
    portal_order(phone="0901234567", shipping_code="SC00003")
    r = public_client.post(TRACK_URL, payload, format="json")
    assert r.status_code == 404
    assert r.data["code"] == "not_found"


def test_track_invalid_phone_format_returns_400(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC00005")
    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00005", "phone": "123"}, format="json"
    )
    assert r.status_code == 400


def test_track_soft_deleted_order_returns_404(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC00006", deleted=True)
    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00006", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 404
    assert r.data["code"] == "not_found"


def test_track_journey_uses_best_events_when_available(public_client, portal_order):
    from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent

    order = portal_order(phone="0901234567", shipping_code="SC00007")
    account = BestExpressAccount.objects.create(name="acc", username="acc-user", password="acc-pass")
    BestShipment.objects.create(
        account=account, order=order, merchant_order_code=f"M{order.id}", code_best=f"B{order.id}"
    )
    BestWebhookEvent.objects.create(
        account=account, code_best=f"B{order.id}", merchant_order_code=f"M{order.id}",
        best_status_code="601", modified_at_ms=1,
    )
    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00007", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 200, r.data
    assert r.data["journey"]["source"] == "best"
    assert r.data["journey"]["results"][0]["label"] == "Đang giao hàng"


def test_track_journey_falls_back_to_internal_without_leaking_note(public_client, portal_order):
    from apps.orders.models import OrderJourneyEvent

    order = portal_order(phone="0901234567", shipping_code="SC00008")
    OrderJourneyEvent.objects.create(
        order=order, event_type="order_status_changed", from_code="0", to_code="2",
        note="ghi chú nội bộ", payload_json={"secret": "x"},
    )
    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC00008", "phone": "0901234567"}, format="json"
    )
    assert r.status_code == 200, r.data
    assert r.data["journey"]["source"] == "internal"
    assert r.data["journey"]["results"][0]["label"] == "Đang giao hàng"
    body = str(r.data)
    assert "ghi chú nội bộ" not in body and "secret" not in body


# --- Bản sửa đổi 24/08: chỉ cần MỘT trong hai ô -----------------------------


def _set_created_at(order, dt):
    """`created_at` là auto_now_add nên phải UPDATE thẳng để cố định thứ tự."""
    from apps.orders.models import Order

    Order.objects.filter(pk=order.pk).update(created_at=dt)


def test_track_with_shipping_code_only_returns_order(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC10001", name="Nguyễn Văn A")

    r = public_client.post(TRACK_URL, {"shipping_code": "sc10001"}, format="json")

    assert r.status_code == 200, r.data
    assert r.data["order"]["shipping_code"] == "SC10001"
    assert "journey" in r.data


def test_track_with_phone_only_single_order_returns_order(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC10002")

    r = public_client.post(TRACK_URL, {"phone": "0901234567"}, format="json")

    assert r.status_code == 200, r.data
    assert r.data["order"]["shipping_code"] == "SC10002"
    assert "journey" in r.data


def test_track_with_phone_only_many_orders_returns_list_newest_first(
    public_client, portal_order
):
    from datetime import datetime

    older = portal_order(phone="0901234567", shipping_code="SC10003")
    newer = portal_order(phone="0901234567", shipping_code="SC10004")
    _set_created_at(older, datetime(2026, 8, 1, tzinfo=UTC))
    _set_created_at(newer, datetime(2026, 8, 20, tzinfo=UTC))

    r = public_client.post(TRACK_URL, {"phone": "0901234567"}, format="json")

    assert r.status_code == 200, r.data
    # Danh sách KHÔNG kèm hành trình: dựng sẵn cho mọi đơn là N+1 trong khi khách
    # chỉ mở một đơn (xem portal_views.track).
    assert "journey" not in r.data
    assert r.data["truncated"] is False
    assert [o["shipping_code"] for o in r.data["orders"]] == ["SC10004", "SC10003"]
    # Mỗi phần tử vẫn là `order_summary` đầy đủ — giao kèo trường giống nhánh 1 đơn.
    assert set(r.data["orders"][0]) == {
        "order_code", "invoice_code", "shipping_code", "created_at", "status", "status_label",
        "customer_name", "phone", "province", "items",
    }


def test_track_list_excludes_soft_deleted_orders(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC10005")
    portal_order(phone="0901234567", shipping_code="SC10006")
    portal_order(phone="0901234567", shipping_code="SC10007", deleted=True)

    r = public_client.post(TRACK_URL, {"phone": "0901234567"}, format="json")

    assert r.status_code == 200, r.data
    codes = {o["shipping_code"] for o in r.data["orders"]}
    assert codes == {"SC10005", "SC10006"}


def test_track_list_caps_at_limit_and_flags_truncated(public_client, portal_order):
    from apps.warranty_codes.transport.portal_views import TRACK_LIST_LIMIT

    for index in range(TRACK_LIST_LIMIT + 2):
        portal_order(phone="0901234567", shipping_code=f"SC2{index:04d}")

    r = public_client.post(TRACK_URL, {"phone": "0901234567"}, format="json")

    assert r.status_code == 200, r.data
    assert len(r.data["orders"]) == TRACK_LIST_LIMIT
    assert r.data["truncated"] is True


def test_track_picks_one_order_from_list_by_order_code(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC10008")
    picked = portal_order(phone="0901234567", shipping_code="SC10009")

    r = public_client.post(
        TRACK_URL, {"phone": "0901234567", "order_code": picked.order_code}, format="json"
    )

    assert r.status_code == 200, r.data
    assert r.data["order"]["shipping_code"] == "SC10009"
    assert "journey" in r.data


def test_track_without_any_key_returns_400(public_client):
    r = public_client.post(TRACK_URL, {}, format="json")

    assert r.status_code == 400


def test_track_with_order_code_only_returns_400(public_client, portal_order):
    """`order_code` chỉ là bộ lọc phụ, không phải một lối vào tra cứu."""
    order = portal_order(phone="0901234567", shipping_code="SC10010")

    r = public_client.post(TRACK_URL, {"order_code": order.order_code}, format="json")

    assert r.status_code == 400


def test_track_blank_phone_with_shipping_code_is_accepted(public_client, portal_order):
    """Ô SĐT để trống không còn bị coi là sai định dạng."""
    portal_order(phone="0901234567", shipping_code="SC10011")

    r = public_client.post(
        TRACK_URL, {"shipping_code": "SC10011", "phone": ""}, format="json"
    )

    assert r.status_code == 200, r.data


def test_track_unknown_phone_returns_404(public_client, portal_order):
    portal_order(phone="0901234567", shipping_code="SC10012")

    r = public_client.post(TRACK_URL, {"phone": "0909999999"}, format="json")

    assert r.status_code == 404
    assert r.data["code"] == "not_found"
