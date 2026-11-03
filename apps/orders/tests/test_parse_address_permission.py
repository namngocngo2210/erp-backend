import pytest
from rest_framework.test import APIClient

from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_warranty_user_can_parse_address():
    """CSKH bảo hành dùng form tạo đơn Best → phải parse được địa chỉ."""
    client = _client(make_user_with_sections("warranty-parse", ["warranty"]))

    response = client.post(
        "/api/orders/parse-address",
        {"raw_address": "12 Lê Lợi, Phường Đại Mỗ, Quận Nam Từ Liêm, Hà Nội"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["province"]


@pytest.mark.django_db
def test_orders_user_still_can_parse_address():
    """Không phá quyền cũ của các trang đơn."""
    client = _client(make_user_with_sections("orders-parse", ["orders"]))

    response = client.post(
        "/api/orders/parse-address", {"raw_address": "12 Lê Lợi, Hà Nội"}, format="json"
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_user_without_any_section_is_denied():
    """Không có quyền liên quan → vẫn 403, không mở toang endpoint."""
    client = _client(make_user_with_permissions("nobody-parse", []))

    response = client.post(
        "/api/orders/parse-address", {"raw_address": "12 Lê Lợi, Hà Nội"}, format="json"
    )

    assert response.status_code == 403
