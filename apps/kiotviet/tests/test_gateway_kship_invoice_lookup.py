"""Gateway KiotViet/KShip: lookup hoá đơn (get_invoice_by_code, timeout, lỗi HTTP),
tạo delivery KShip khi KiotViet trả Data rỗng, và payload tạo khách hàng.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.kiotviet.infrastructure.services import common_auth_customer
from apps.kiotviet.infrastructure.services.common_auth_customer import (
    KiotVietApiError,
    KiotVietInvoiceNotFound,
)
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    build_kship_create_order_payload,
    create_customer,
    create_kship_delivery_for_invoice,
    list_shipper_note_options,
)
from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code
from apps.kiotviet.models import KiotVietChannel

pytestmark = pytest.mark.django_db

_GATEWAY = "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook"
# `_kiotviet_request` giờ định nghĩa duy nhất ở common_auth_customer (gateway chỉ re-export)
# nên các lời gọi bên trong nó (get_access_token) resolve qua globals của module NÀY.
_AUTH = "apps.kiotviet.infrastructure.services.common_auth_customer"


def _channel() -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        name=f"ch-{uuid.uuid4().hex[:8]}",
        client_id="cid",
        client_secret="secret",
        retailer="retailer",
    )


def _response_with_status(status_code: int, body: str = "{}", payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.json.return_value = payload if payload is not None else {}
    return resp


def test_default_timeout_is_30_when_caller_does_not_pass_one():
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok"),
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(200, payload={"status": 1})) as req,
    ):
        assert get_invoice_by_code("HD1", None, channel) == {"status": 1}
    assert req.call_args.kwargs["timeout"] == 30


def test_caller_timeout_is_forwarded_to_requests():
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok"),
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(200, payload={"status": 1})) as req,
    ):
        assert get_invoice_by_code("HD1", None, channel, timeout=3.0) == {"status": 1}
    assert req.call_args.kwargs["timeout"] == 3.0


def test_zero_timeout_is_not_silently_replaced_by_30():
    """M1: `or 30` biến timeout=0 thành 30 im lặng — phải giữ nguyên giá trị caller truyền."""
    channel = _channel()
    with (
        patch.object(common_auth_customer, "get_access_token", return_value="tok"),
        patch.object(common_auth_customer.http_read, "request", return_value=_response_with_status(200)) as req,
    ):
        common_auth_customer._kiotviet_request(
            order=None, channel=channel, method="GET", endpoint="/ping", timeout=0
        )
    assert req.call_args.kwargs["timeout"] == 0


def test_caller_timeout_also_applies_to_token_refresh():
    """Hot path quét kho: token vừa hết hạn KHÔNG được treo 30s dù caller xin timeout 3s."""
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok") as token,
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(200, payload={"status": 1})),
    ):
        get_invoice_by_code("HD1", None, channel, timeout=3.0)
    assert token.call_args.kwargs["timeout"] == 3.0


def test_token_refresh_keeps_30s_default_when_caller_passes_nothing():
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok") as token,
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(200, payload={"status": 1})),
    ):
        get_invoice_by_code("HD1", None, channel)
    assert token.call_args.kwargs["timeout"] is None  # get_access_token tự dùng 30s


def test_get_access_token_uses_caller_timeout_on_http_post():
    channel = _channel()
    with patch.object(
        common_auth_customer.http_read,
        "post",
        return_value=_response_with_status(200, payload={"access_token": "tok", "expires_in": 3600}),
    ) as post:
        common_auth_customer.get_access_token(None, channel, timeout=3.0)
    assert post.call_args.kwargs["timeout"] == 3.0


def test_404_raises_invoice_not_found_with_status_code():
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok"),
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(404, body="not found")),
    ):
        with pytest.raises(KiotVietInvoiceNotFound) as exc:
            get_invoice_by_code("HD-ghost", None, channel)
    assert exc.value.status_code == 404
    # Backward-compat: caller cũ chỉ `except Exception` vẫn bắt được.
    assert isinstance(exc.value, Exception)


def test_server_error_raises_api_error_with_status_code():
    channel = _channel()
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok"),
        patch(f"{_AUTH}.http_read.request", return_value=_response_with_status(500, body="boom")),
    ):
        with pytest.raises(KiotVietApiError) as exc:
            get_invoice_by_code("HD1", None, channel)
    assert exc.value.status_code == 500
    assert not isinstance(exc.value, KiotVietInvoiceNotFound)


# --- create_kship_delivery_for_invoice: KiotViet trả Data rỗng -----------------
# create_kship_delivery_for_invoice phải trả error message tử tế khi KiotViet trả Data rỗng.
#
# Bug prod 2026-08-04: nhánh Data rỗng / thiếu DeliveryId dùng `_json.dumps` nhưng
# `_json` chỉ là import cục bộ trong create_kship_order → NameError nuốt mất
# error message thật (job ghi "name '_json' is not defined").


def _response_with_payload(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"{}"
    resp.json.return_value = payload
    return resp


def _call(payload: dict) -> dict:
    order = MagicMock(id=1)
    channel = MagicMock(id=4)
    with patch(f"{_GATEWAY}._kiotviet_web_request", return_value=_response_with_payload(payload)):
        return create_kship_delivery_for_invoice(123, order, channel)


def test_empty_data_returns_readable_error():
    result = _call({"Data": [], "Total": 0})
    assert result["success"] is False
    assert "_json" not in result["error"]
    assert "không trả dữ liệu delivery" in result["error"]
    # Response gốc phải nằm trong message để trace được trên job record.
    assert '"Total": 0' in result["error"]


def test_missing_delivery_id_returns_readable_error():
    result = _call({"Data": [{"Id": 9, "Code": "HD1"}], "Total": 1})
    assert result["success"] is False
    assert "_json" not in result["error"]
    assert "không trả DeliveryId" in result["error"]


# --- build_kship_create_order_payload: SERVICE_EXTRA ---------------------------
# ShipperNote do người dùng chọn theo từng đơn. Bảng mã ở backend là nguồn sự thật duy
# nhất; mã lạ phải rơi về mặc định chứ không được làm hỏng cả lượt tạo vận đơn.


def _kship_payload(shipper_note: str = "") -> dict:
    delivery_data = {
        "Id": 9,
        "Code": "HD1",
        "ReceiverName": "Nguyen Van A",
        "ReceiverPhone": "0900000000",
        "ReceiverAddress": "1 Duong Test",
        "ReceiverLocation": {"Id": 1, "Name": "Ha Noi"},
        "ReceiverWard": {"Id": 2, "Name": "Phuong 1"},
        "Weight": 500,
        "Length": 10,
        "Width": 10,
        "Height": 10,
        "ProductQuantity": 1,
        "ProductPrice": 500000,
        "CodNeedPayment": 500000,
        "Products": [{"Name": "San pham"}],
    }
    sender_info = {"location_id": 3, "ward_id": 4, "address": "Kho A", "mobile": "0900000001"}
    return build_kship_create_order_payload(
        delivery_data,
        "BEST",
        "STANDARD",
        sender_info,
        note="giao gio hanh chinh",
        shipper_note=shipper_note,
    )


def _service_extra_by_code(payload: dict) -> dict:
    return {item["Code"]: item for item in payload["SERVICE_EXTRA"]}


def test_shipper_note_defaults_to_view_without_trying():
    """Không chọn gì thì cho khách xem hàng nhưng KHÔNG cho thử."""
    entry = _service_extra_by_code(_kship_payload())["ShipperNote"]
    assert entry["Value"] == "CHOXEMHANGKHONGTHU"
    assert entry["Name"] == "Cho xem hàng không thử"


def test_shipper_note_honours_explicit_choice():
    entry = _service_extra_by_code(_kship_payload("KHONGCHOXEMHANG"))["ShipperNote"]
    assert entry["Value"] == "KHONGCHOXEMHANG"
    # Name lấy từ bảng nên luôn khớp Code — không còn ghép tay hai chuỗi rời.
    assert entry["Name"] == "Không cho xem hàng"


def test_unknown_shipper_note_falls_back_with_warning(caplog):
    """Mã lạ KHÔNG được ném exception: một trường phụ không được làm hỏng cả lượt tạo."""
    with caplog.at_level(logging.WARNING):
        entry = _service_extra_by_code(_kship_payload("KHONG_CO_MA_NAY"))["ShipperNote"]
    assert entry["Value"] == "CHOXEMHANGKHONGTHU"
    assert any("KHONG_CO_MA_NAY" in record.getMessage() for record in caplog.records)


def test_shipper_note_options_expose_exactly_one_default():
    options = list_shipper_note_options()
    defaults = [item for item in options if item["is_default"]]
    assert [item["code"] for item in defaults] == ["CHOXEMHANGKHONGTHU"]
    assert all(item["code"] and item["name"] for item in options)


def test_payment_by_and_declared_value_stay_unchanged():
    extra = _service_extra_by_code(_kship_payload())
    assert extra["PaymentBy"]["Value"] == "NGUOIGUI"
    assert extra["GBH"]["Value"] == 500000


def test_note_and_cod_are_forwarded_to_payload():
    payload = _kship_payload()
    assert payload["NOTE"] == "giao gio hanh chinh"
    assert payload["MONEY_COLLECTION"] == 500000
    assert payload["ORDER_SERVICE"] == "STANDARD"


# --- create_customer: trường hoá đơn điện tử ----------------------------------
# KiotViet giữ bộ trường HĐĐT riêng; thiếu thì hoá đơn xuất ra trống thông tin người mua.


def _create_customer_request_payload(customer_data: dict) -> dict:
    channel = _channel()
    response = _response_with_status(200, payload={"code": "KH1", "id": 7})
    with (
        patch(f"{_AUTH}.get_access_token", return_value="tok"),
        # POST /customers là mutation → đi qua http_mutate (không retry).
        patch(f"{_AUTH}.http_mutate.request", return_value=response) as req,
    ):
        create_customer(customer_data, order=None, channel=channel)
    return req.call_args.kwargs["json"]


def test_create_customer_mirrors_einvoice_fields():
    payload = _create_customer_request_payload(
        {
            "code": "KH1",
            "name": "Nguyễn Văn A",
            "contactNumber": "0901234567",
            "address": "12 Lê Lợi, Quận 1",
        }
    )
    assert payload["ContactNumberEInvoice"] == "0901234567"
    assert payload["NameEInvoice"] == "Nguyễn Văn A"
    assert payload["AddressEInvoice"] == "12 Lê Lợi, Quận 1"


def test_create_customer_keeps_explicit_einvoice_values():
    payload = _create_customer_request_payload(
        {
            "code": "KH1",
            "name": "Nguyễn Văn A",
            "contactNumber": "0901234567",
            "address": "12 Lê Lợi, Quận 1",
            "NameEInvoice": "CÔNG TY TNHH ABC",
        }
    )
    assert payload["NameEInvoice"] == "CÔNG TY TNHH ABC"
    assert payload["ContactNumberEInvoice"] == "0901234567"


def test_create_customer_omits_einvoice_fields_when_source_empty():
    payload = _create_customer_request_payload({"code": "KH1", "name": "Khách lẻ", "address": ""})
    assert payload["NameEInvoice"] == "Khách lẻ"
    assert "ContactNumberEInvoice" not in payload
    assert "AddressEInvoice" not in payload
