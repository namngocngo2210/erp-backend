"""SERVICE_EXTRA bắt buộc theo hãng (auto_add=1) khi tạo vận đơn KShip.

Payload create-order trước đây hardcode đúng 3 mục (PaymentBy/GBH/ShipperNote) cho
mọi hãng. SPX khai thêm ``PickShift`` (ca lấy hàng) với ``auto_add: 1`` và giá trị
đổi theo NGÀY (``"<epoch ngày>-<số ca>"``) nên không hardcode được — phải đọc từ
setting-service ngay lúc tạo đơn. J&T thì không có mục bắt buộc nào ngoài 3 mục cũ.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    _collect_service_extra_choices,
    build_kship_create_order_payload,
    resolve_required_service_extra,
)


# resolve_required_service_extra cache 60s theo (channel, provider, service);
# mỗi test một LocMemCache riêng (backend/conftest.py) để test sau không ăn kết quả test trước.
pytestmark = pytest.mark.usefixtures("isolated_cache")

_PICK_SHIFT_OPTIONS = json.dumps(
    [
        {"Code": "PickShift", "Value": "1789030800-1", "ViewType": "DropdownList", "Name": "Ca lấy 10-09 (10h-18h)"},
        {"Code": "PickShift", "Value": "1789030800-2", "ViewType": "DropdownList", "Name": "Ca lấy 10-09 (18h-21h)"},
    ]
)


def _clients_payload() -> dict:
    return {
        "data": {
            "clients": {
                "SPX": {
                    "id": 17,
                    "code": "SPX",
                    "name": "SPX Express",
                    "status": 1,
                    "services": [{"id": 186, "code": "1", "name": "Tiêu chuẩn"}],
                    "services_extra": [
                        # Đã nằm trong 3 mục mặc định → không được thêm lần hai.
                        {"id": 48, "code": "GBH", "name": "Khai giá", "view_type": "CheckList",
                         "auto_add": 1, "options": "", "services_possible": ["186"]},
                        {"id": 194, "code": "PickShift", "name": "Chọn ca lấy hàng",
                         "view_type": "DropdownList", "auto_add": 1, "options": _PICK_SHIFT_OPTIONS,
                         "services_possible": ["72", "280", "256", "162", "186"]},
                        # auto_add=0 → tuỳ chọn, không tự thêm.
                        {"id": 209, "code": "GTBTT", "name": "Giao thất bại - thu tiền",
                         "view_type": "CheckListText", "auto_add": 0, "options": "",
                         "services_possible": ["186"]},
                    ],
                },
                "J&T": {
                    "id": 7,
                    "code": "J&T",
                    "name": "J&T",
                    "status": 1,
                    "services": [{"id": 287, "code": "EXPRESS", "name": "Express"}],
                    "services_extra": [
                        {"id": 48, "code": "GBH", "name": "Khai giá", "view_type": "CheckList",
                         "auto_add": 0, "options": "", "services_possible": ["287"]},
                    ],
                },
            }
        }
    }


def _patched_setting_service(payload: dict | None = None, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.content = b"{}"
    response.text = "{}"
    response.json.return_value = payload if payload is not None else _clients_payload()
    return patch(
        "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook."
        "_request_kship_setting_service",
        return_value=response,
    )


def test_spx_requires_pick_shift_and_uses_first_available_shift():
    with _patched_setting_service():
        extras = resolve_required_service_extra(MagicMock(id=4), "SPX", "1")

    assert extras == [
        {
            "Code": "PickShift",
            "Name": "Chọn ca lấy hàng",
            "ViewType": "DropdownList",
            "Value": "1789030800-1",
        }
    ]


def test_jt_has_no_required_extra_beyond_the_default_three():
    with _patched_setting_service():
        assert resolve_required_service_extra(MagicMock(id=4), "J&T", "EXPRESS") == []


def test_extra_not_applicable_to_the_chosen_service_is_skipped():
    payload = _clients_payload()
    payload["data"]["clients"]["SPX"]["services_extra"][1]["services_possible"] = ["72", "280"]
    with _patched_setting_service(payload):
        assert resolve_required_service_extra(MagicMock(id=4), "SPX", "1") == []


def test_setting_service_failure_does_not_break_order_creation():
    # Không lấy được cấu hình thì để KShip tự từ chối với lỗi của hãng, chứ không
    # ném exception cắt ngang lượt tạo vận đơn (cùng nguyên tắc với resolve_shipper_note).
    with patch(
        "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook."
        "_request_kship_setting_service",
        side_effect=Exception("boom"),
    ):
        assert resolve_required_service_extra(MagicMock(id=4), "SPX", "1") == []


def _delivery_data() -> dict:
    return {
        "ReceiverName": "Khach A",
        "ReceiverPhone": "0900000000",
        "ReceiverAddress": "1 Duong X",
        "ReceiverLocation": {"Id": 1, "Name": "Ha Noi"},
        "ReceiverWard": {"Id": 2, "Name": "Phuong Y"},
        "Weight": 1000,
        "Length": 10,
        "Width": 10,
        "Height": 10,
        "ProductQuantity": 1,
        "ProductPrice": 500000,
        "CodNeedPayment": 500000,
        "Products": [{"Name": "Ghe massage"}],
        "Code": "HD1",
        "Id": 99,
    }


def _sender_info() -> dict:
    return {"location_id": 1, "ward_id": 2, "address": "Kho A", "mobile": "0911111111"}


def test_payload_appends_required_extra_after_the_default_three():
    payload = build_kship_create_order_payload(
        _delivery_data(),
        "SPX",
        "1",
        _sender_info(),
        extra_services=[
            {"Code": "PickShift", "Name": "Chọn ca lấy hàng", "ViewType": "DropdownList",
             "Value": "1789030800-1"}
        ],
    )

    codes = [item["Code"] for item in payload["SERVICE_EXTRA"]]
    assert codes == ["PaymentBy", "GBH", "ShipperNote", "PickShift"]


def test_payload_never_duplicates_a_default_extra_code():
    payload = build_kship_create_order_payload(
        _delivery_data(),
        "SPX",
        "1",
        _sender_info(),
        extra_services=[
            {"Code": "GBH", "Name": "Khai giá", "ViewType": "CheckList", "Value": "1"}
        ],
    )

    codes = [item["Code"] for item in payload["SERVICE_EXTRA"]]
    assert codes == ["PaymentBy", "GBH", "ShipperNote"]
    # Giá trị khai giá vẫn là giá hàng do builder tính, không bị mục thêm ghi đè.
    gbh = next(item for item in payload["SERVICE_EXTRA"] if item["Code"] == "GBH")
    assert gbh["Value"] == 500000


def test_payload_without_extra_services_is_unchanged():
    payload = build_kship_create_order_payload(_delivery_data(), "GHN", "2", _sender_info())
    codes = [item["Code"] for item in payload["SERVICE_EXTRA"]]
    assert codes == ["PaymentBy", "GBH", "ShipperNote"]


def test_result_is_cached_so_bulk_creation_does_not_refetch_setting_service():
    with _patched_setting_service() as mocked:
        first = resolve_required_service_extra(MagicMock(id=4), "SPX", "1")
        second = resolve_required_service_extra(MagicMock(id=4), "SPX", "1")

    assert first == second
    assert mocked.call_count == 1


def test_failure_is_not_cached():
    channel = MagicMock(id=4)
    with patch(
        "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook."
        "_request_kship_setting_service",
        side_effect=Exception("boom"),
    ):
        assert resolve_required_service_extra(channel, "SPX", "1") == []

    with _patched_setting_service():
        assert resolve_required_service_extra(channel, "SPX", "1") != []


def test_choices_expose_options_for_the_dialog():
    choices = _collect_service_extra_choices(_clients_payload())

    assert choices == [
        {
            "provider_code": "SPX",
            "service_code": "1",
            "code": "PickShift",
            "name": "Chọn ca lấy hàng",
            "view_type": "DropdownList",
            "options": [
                {"value": "1789030800-1", "name": "Ca lấy 10-09 (10h-18h)"},
                {"value": "1789030800-2", "name": "Ca lấy 10-09 (18h-21h)"},
            ],
        }
    ]


def test_user_selected_shift_wins_over_the_earliest_one():
    with _patched_setting_service():
        extras = resolve_required_service_extra(
            MagicMock(id=4), "SPX", "1", selected={"PickShift": "1789030800-2"}
        )

    assert [item["Value"] for item in extras] == ["1789030800-2"]


def test_selected_shift_no_longer_offered_falls_back_to_earliest():
    # Ca người dùng chọn có thể hết hạn giữa lúc mở dialog và lúc worker chạy job.
    with _patched_setting_service():
        extras = resolve_required_service_extra(
            MagicMock(id=4), "SPX", "1", selected={"PickShift": "1700000000-9"}
        )

    assert [item["Value"] for item in extras] == ["1789030800-1"]
