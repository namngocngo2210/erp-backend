"""Tiện ích › Tra cứu địa chỉ: 3 cấp (63 tỉnh) + 2 cấp (34 tỉnh)."""

import pytest
from rest_framework.test import APIClient

from apps.orders.address_lookup import lookup_address
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections


def test_lookup_converts_old_ward_to_new_ward():
    result = lookup_address("Số 5 ngõ 12 phố Chùa Láng, Láng Thượng, Đống Đa, Hà Nội")

    assert result["level3"]["province"] == "Thành phố Hà Nội"
    assert result["level3"]["district"] == "Quận Đống Đa"
    assert result["level3"]["ward"] == "Phường Láng Thượng"
    assert result["level3"]["address"].endswith("Phường Láng Thượng, Quận Đống Đa, Thành phố Hà Nội")

    # Láng Thượng (Đống Đa) sáp nhập thành Phường Láng từ 01/07/2025.
    assert result["level2"]["province"] == "Thành phố Hà Nội"
    assert result["level2"]["ward"] == "Phường Láng"
    assert result["level2"]["source"] == "converted"
    assert result["level2"]["address"].endswith("Phường Láng, Thành phố Hà Nội")
    assert "Quận" not in result["level2"]["address"]


def test_lookup_accepts_address_already_in_new_format():
    result = lookup_address("phường xuân hòa, tp hồ chí minh")

    assert result["level2"]["ward"] == "Phường Xuân Hòa"
    assert result["level2"]["province"] == "Thành phố Hồ Chí Minh"
    assert result["level2"]["source"] == "parsed"


def test_lookup_empty_and_garbage_input():
    assert lookup_address("") == lookup_address("   ")
    assert lookup_address("")["level3"]["address"] == ""
    garbage = lookup_address("abc xyz")
    assert garbage["level3"]["province"] == ""
    assert garbage["level2"]["province"] == ""
    assert garbage["level2"]["source"] == ""


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_api_requires_address_lookup_section():
    client = _client(make_user_with_sections("lookup-ok", ["address-lookup"]))
    response = client.post(
        "/api/orders/lookup-address",
        {"raw_address": "thôn 3 xã ea kly huyện krông pắc đắk lắk"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["level3"]["district"] == "Huyện Krông Pắc"
    assert response.data["level2"]["ward"] == "Xã Ea Kly"


@pytest.mark.django_db
def test_api_orders_section_alone_is_denied():
    """Quyền đơn hàng KHÔNG kéo theo tiện ích — section riêng, cấp qua role."""
    client = _client(make_user_with_sections("lookup-orders", ["orders"]))
    response = client.post("/api/orders/lookup-address", {"raw_address": "Hà Nội"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_api_anonymous_denied():
    response = APIClient().post("/api/orders/lookup-address", {"raw_address": "Hà Nội"}, format="json")
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_no_permission_denied():
    client = _client(make_user_with_permissions("lookup-nobody", []))
    response = client.post("/api/orders/lookup-address", {"raw_address": "Hà Nội"}, format="json")
    assert response.status_code == 403
