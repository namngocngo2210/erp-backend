"""Che SĐT khách của đơn KHÔNG do mình phụ trách (quyền `customer-phone:view`).

Luật (chốt 2026-09-22): sale giữ nguyên scope rộng — vẫn THẤY và TÌM được mọi đơn —
nhưng SĐT khách trên đơn của sale khác chỉ hiện 3 số đầu + 3 số cuối. Xem
apps/users/phone_visibility.py.

Bốn mặt phải khoá cùng lúc, thiếu mặt nào là vô hiệu hoá cả tính năng:
list/detail · tìm kiếm · file Excel · luồng GHI (không để bản che đè lên số gốc).
"""

import pytest

from apps.common.masking import mask_phone_middle
from apps.orders.export_excel import order_row_values
from apps.users.phone_visibility import CUSTOMER_PHONE_PERMISSION, phone_visibility_for
from apps.users.tests.helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db

_PHONE = "0912345678"
_MASKED = "091****678"


def _client_for(user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def sale_no_phone_permission():
    """Sale scope ALL: xem được mọi đơn, KHÔNG có `customer-phone:view`."""
    from apps.users.models import Permission

    codes = list(
        Permission.objects.filter(section="orders").values_list("code", flat=True)
    ) or [f"orders:{a}" for a in ("view", "create", "edit", "delete", "export")]
    return make_user_with_permissions("sale-masked-phone", codes)


@pytest.fixture
def sale_with_phone_permission():
    """Quản lý/kho: cùng scope nhưng CÓ `customer-phone:view` → thấy số thật."""
    return make_user_with_permissions(
        "sale-full-phone", ["orders:view", "orders:export", CUSTOMER_PHONE_PERMISSION]
    )


@pytest.fixture
def other_sale():
    return make_user_with_permissions("sale-owner-other", ["orders:view"])


def _order_of(order_factory, staff, **kwargs):
    from apps.customers.models import Customer

    customer = Customer.objects.create(
        full_name="Khach mask", phone_raw=_PHONE, phone_normalized=_PHONE
    )
    return order_factory(
        staff=staff, customer=customer, phone=_PHONE, phone_normalized=_PHONE, **kwargs
    )


def _row_for(client, order_id):
    response = client.post("/api/orders", {"limit": "50", "offset": "0"}, format="json")
    assert response.status_code == 200, response.content
    rows = [r for r in response.json()["results"] if r["id"] == order_id]
    assert rows, "đơn không xuất hiện trong danh sách"
    return rows[0]


def test_mask_keeps_three_digits_each_end():
    assert mask_phone_middle(_PHONE) == _MASKED
    # Chuỗi ngắn hơn 6 ký tự không đủ để chừa hai đầu → che sạch.
    assert mask_phone_middle("0912") == "****"
    assert mask_phone_middle("") == ""


def test_list_masks_phone_of_other_sales_order(
    order_factory, sale_no_phone_permission, other_sale
):
    order = _order_of(order_factory, other_sale)

    row = _row_for(_client_for(sale_no_phone_permission), order.id)

    assert row["phone"] == _MASKED
    assert row["customer_phone"] == _MASKED


def test_marketplace_order_phone_is_not_masked(
    order_factory, sale_no_phone_permission, other_sale
):
    """Đơn sàn (Shopee/TikTok): KHÔNG che dù không phải đơn của mình.

    Sàn đã trả SĐT che sẵn (TikTok Shop: `(+84)037*****54`); che thêm một lớp nữa chỉ
    ăn mất chữ số còn lại. Số của sàn cũng không phải tệp khách do sale gây dựng nên
    không thuộc diện "tài sản cạnh tranh giữa sale".
    """
    from apps.orders.models import OrderSource

    order = _order_of(order_factory, other_sale, order_source=OrderSource.SHOPEE)

    row = _row_for(_client_for(sale_no_phone_permission), order.id)

    assert row["phone"] == _PHONE
    assert row["customer_phone"] == _PHONE


def test_marketplace_source_codes_match_orders_catalog():
    """Hằng ở apps.common (consumer WS dùng) không được lệch khỏi nguồn sự thật bên orders.

    Lệch một giá trị là realtime che khác REST — đúng kiểu lỗi im lặng mà bản sao
    hằng số hay đẻ ra.
    """
    from apps.common.business_constants import MARKETPLACE_ORDER_SOURCE_CODES
    from apps.orders.models import MARKETPLACE_ORDER_SOURCES

    assert set(MARKETPLACE_ORDER_SOURCES) == set(MARKETPLACE_ORDER_SOURCE_CODES)


def test_already_masked_phone_is_left_untouched():
    """Số đã che từ nguồn (đơn sàn cũ đồng bộ về) không bị che chồng lên."""
    assert mask_phone_middle("(+84)037*****54") == "(+84)037*****54"


def test_list_shows_full_phone_of_own_order(order_factory, sale_no_phone_permission):
    order = _order_of(order_factory, sale_no_phone_permission)

    row = _row_for(_client_for(sale_no_phone_permission), order.id)

    assert row["phone"] == _PHONE
    assert row["customer_phone"] == _PHONE


def test_permission_holder_sees_full_phone(
    order_factory, sale_with_phone_permission, other_sale
):
    order = _order_of(order_factory, other_sale)

    row = _row_for(_client_for(sale_with_phone_permission), order.id)

    assert row["phone"] == _PHONE


def test_admin_sees_full_phone(order_factory, other_sale):
    order = _order_of(order_factory, other_sale)

    row = _row_for(_client_for(make_admin("admin-phone-mask")), order.id)

    assert row["phone"] == _PHONE


def test_detail_endpoint_masks_phone(order_factory, sale_no_phone_permission, other_sale):
    order = _order_of(order_factory, other_sale)

    response = _client_for(sale_no_phone_permission).get(f"/api/orders/{order.id}")

    assert response.status_code == 200, response.content
    assert response.json()["phone"] == _MASKED


def test_search_still_finds_order_by_full_phone_but_masks_result(
    order_factory, sale_no_phone_permission, other_sale
):
    """Yêu cầu nghiệp vụ: TÌM vẫn bình thường, chỉ ĐẦU RA bị che."""
    order = _order_of(order_factory, other_sale)

    response = _client_for(sale_no_phone_permission).get(f"/api/orders/search?q={_PHONE}")

    assert response.status_code == 200, response.content
    rows = [r for r in response.json() if r["id"] == order.id]
    assert rows, "tìm theo số đầy đủ phải vẫn ra đơn"
    assert rows[0]["phone"] == _MASKED


def test_excel_export_row_masks_phone(order_factory, sale_no_phone_permission, other_sale):
    order = _order_of(order_factory, other_sale)
    columns = [{"key": "phone", "header": "SĐT"}]

    values = order_row_values(order, columns, phone_visibility_for(sale_no_phone_permission))

    assert values == [_MASKED]


def test_excel_export_row_is_masked_when_visibility_missing(order_factory, other_sale):
    """Fail-closed: quên truyền visibility thì che, không rò cả tệp khách ra file."""
    order = _order_of(order_factory, other_sale)

    assert order_row_values(order, [{"key": "phone", "header": "SĐT"}]) == [_MASKED]


def test_patch_with_masked_phone_does_not_overwrite_original(
    order_factory, sale_no_phone_permission, other_sale
):
    """FE gửi lại chuỗi đã che khi lưu form → bỏ qua, giữ nguyên số gốc (không 400)."""
    from apps.orders.models import Order
    from apps.users.tests.helpers import grant_order_fields

    order = _order_of(order_factory, other_sale)
    # CỐ Ý không cấp field "phone": bản che bị bỏ khỏi payload trước cả guard quyền
    # sửa-theo-field, nên lần lưu này không được 403 oan.
    grant_order_fields(sale_no_phone_permission, "feedback")

    response = _client_for(sale_no_phone_permission).patch(
        f"/api/orders/{order.id}",
        data={"phone": _MASKED, "feedback": "ghi chu moi"},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    order = Order.objects.get(pk=order.pk)
    assert order.phone == _PHONE
    assert order.feedback == "ghi chu moi"


def test_patch_with_real_phone_still_updates(
    order_factory, sale_no_phone_permission, other_sale
):
    from apps.orders.models import Order
    from apps.users.tests.helpers import grant_order_fields

    order = _order_of(order_factory, other_sale)
    grant_order_fields(sale_no_phone_permission, "phone")

    response = _client_for(sale_no_phone_permission).patch(
        f"/api/orders/{order.id}",
        data={"phone": "0987654321"},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert Order.objects.get(pk=order.pk).phone == "0987654321"
