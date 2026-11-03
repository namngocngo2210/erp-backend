"""Timeout khi POST create-order KShip: vận đơn có thể ĐÃ được tạo thật.

`ReadTimeout` nghĩa là request đã bay đi và KShip đã xử lý, chỉ có phản hồi về không
kịp — khác hẳn lỗi nghiệp vụ (KShip từ chối). Coi hai thứ là một chính là lý do đơn
554748 có vận đơn thật trên KiotViet nhưng `shipping_code` trong DB vẫn rỗng
(2026-08-18). Bộ test này khoá hai hành vi:

1. Gateway phân loại được lỗi truyền tải thành `indeterminate` thay vì `success=False` trơn.
2. Action `create_kship_delivery` gặp `indeterminate` thì ĐỌC LẠI hoá đơn KiotViet để
   lấy `invoiceDelivery.deliveryCode` (đúng đường mà `sync_shipping` vẫn dùng) và ghi
   vào `order.shipping_code`, thay vì bỏ mặc vận đơn thật mồ côi bên đơn vị vận chuyển.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    create_kship_order,
)
from apps.kiotviet.infrastructure.services.service_actions import KiotVietService
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

_GATEWAY = "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook"
_ACTIONS = "apps.kiotviet.infrastructure.services.service_actions"


def _kiotviet_channel() -> KiotVietChannel:
    suffix = uuid.uuid4().hex[:8]
    return KiotVietChannel.objects.create(
        name=f"kv-{suffix}", retailer=f"r-{suffix}", old=False, username="u", password="p",
    )


def _order(kv: KiotVietChannel) -> Order:
    suffix = uuid.uuid4().hex[:8]
    channel = Channel.objects.create(code=f"kship-{suffix}", name=f"KShip {suffix}")
    customer = Customer.objects.create(full_name=f"KH {suffix}", phone_raw=f"09{uuid.uuid4().int % 10**8:08d}")
    return Order.objects.create(
        channel=channel,
        customer=customer,
        kiotviet_channel=kv,
        invoice_id=778899,
        invoice_code="HD-778899",
        shipping_code="",
    )


def _response(status_code: int, payload: dict | None = None, body: str = "{}") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.content = body.encode()
    resp.json.return_value = payload if payload is not None else {}
    return resp


def _invoice_with_delivery_code(code: str) -> dict:
    return {"code": "HD-778899", "invoiceDelivery": {"deliveryCode": code}}


def _delivery_step1(delivery_code: str = "") -> dict:
    """Kết quả bước 1 (invoicesForMultiOrderDeliveryCreate) đã chuẩn hoá."""
    return {
        "success": True,
        "delivery_id": 4242,
        "delivery_code": delivery_code,
        "delivery_data": {"Id": 778899, "Code": "HD-778899", "ReceiverName": "A"},
    }


# --------------------------------------------------------------------------------------
# Gateway: phân loại lỗi truyền tải
# --------------------------------------------------------------------------------------


def test_read_timeout_is_flagged_indeterminate_not_plain_failure():
    """Timeout = KHÔNG BIẾT kết quả. Gộp chung với lỗi nghiệp vụ là mất vận đơn thật."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_GATEWAY}.get_web_access_token", return_value="tok"),
        patch(
            f"{_GATEWAY}.http_mutate.post",
            side_effect=requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)"),
        ),
    ):
        result = create_kship_order("GHN", {}, order, order.kiotviet_channel)

    assert result["success"] is False
    assert result["indeterminate"] is True


def test_connection_error_is_flagged_indeterminate():
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_GATEWAY}.get_web_access_token", return_value="tok"),
        patch(f"{_GATEWAY}.http_mutate.post", side_effect=requests.exceptions.ConnectionError("boom")),
    ):
        result = create_kship_order("GHN", {}, order, order.kiotviet_channel)

    assert result["indeterminate"] is True


def test_gateway_5xx_is_flagged_indeterminate():
    """502/503/504 là lỗi ở tầng gateway — request vẫn có thể đã tới KShip."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_GATEWAY}.get_web_access_token", return_value="tok"),
        patch(f"{_GATEWAY}.http_mutate.post", return_value=_response(504, body="gateway timeout")),
    ):
        result = create_kship_order("GHN", {}, order, order.kiotviet_channel)

    assert result["success"] is False
    assert result["indeterminate"] is True


def test_business_rejection_is_not_indeterminate():
    """KShip trả 400 kèm lý do = biết chắc KHÔNG có vận đơn; đừng đi dò lại vô ích."""
    order = _order(_kiotviet_channel())
    body = '{"data": {"message": "Sai địa chỉ nhận"}}'
    with (
        patch(f"{_GATEWAY}.get_web_access_token", return_value="tok"),
        patch(
            f"{_GATEWAY}.http_mutate.post",
            return_value=_response(400, payload={"data": {"message": "Sai địa chỉ nhận"}}, body=body),
        ),
    ):
        result = create_kship_order("GHN", {}, order, order.kiotviet_channel)

    assert result["success"] is False
    assert result.get("indeterminate") is not True
    assert result["error"] == "Sai địa chỉ nhận"


def test_create_order_timeout_is_configurable_and_defaults_to_60():
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_GATEWAY}.get_web_access_token", return_value="tok"),
        patch(
            f"{_GATEWAY}.http_mutate.post",
            return_value=_response(200, payload={"data": {"ORDER_NUMBER": "GHN1"}}),
        ) as post,
    ):
        create_kship_order("GHN", {}, order, order.kiotviet_channel)

    assert post.call_args.kwargs["timeout"] == 60


# --------------------------------------------------------------------------------------
# Action: dò lại mã vận đơn sau timeout
# --------------------------------------------------------------------------------------


def _run_action(order: Order):
    return KiotVietService.execute_action(
        order,
        KiotVietService.ACTION_CREATE_KSHIP_DELIVERY,
        provider_code="GHN",
        service_code="STD",
    )


def test_timeout_then_reconcile_finds_waybill_saves_shipping_code():
    """Đúng ca đơn 554748: KShip đã tạo vận đơn, hoá đơn KiotViet đã có deliveryCode."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": False, "indeterminate": True, "error": "Read timed out."},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code", return_value=_invoice_with_delivery_code("GHN9988")),
        patch(f"{_ACTIONS}.time.sleep"),
    ):
        result = _run_action(order)

    order.refresh_from_db()
    assert order.shipping_code == "GHN9988"
    assert result["status"] == "created"
    assert result["payload"]["recovered_by"] == "reconcile_after_indeterminate"


def test_reconcile_retries_until_kiotviet_catches_up():
    """KShip đẩy mã sang KiotViet có độ trễ — lần đọc đầu rỗng không có nghĩa là thất bại."""
    order = _order(_kiotviet_channel())
    reads = [
        _invoice_with_delivery_code(""),
        _invoice_with_delivery_code(""),
        _invoice_with_delivery_code("GHN7766"),
    ]
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": False, "indeterminate": True, "error": "Read timed out."},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code", side_effect=reads) as get_invoice,
        patch(f"{_ACTIONS}.time.sleep"),
    ):
        result = _run_action(order)

    order.refresh_from_db()
    assert get_invoice.call_count == 3
    assert order.shipping_code == "GHN7766"
    assert result["status"] == "created"


def test_reconcile_finds_nothing_reports_indeterminate_and_warns_against_retry():
    """Không dò ra thì PHẢI nói rõ là 'không xác định', không được để user bấm tạo lại mù."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": False, "indeterminate": True, "error": "Read timed out."},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code", return_value=_invoice_with_delivery_code("")),
        patch(f"{_ACTIONS}.time.sleep"),
    ):
        result = _run_action(order)

    order.refresh_from_db()
    assert order.shipping_code == ""
    assert result["status"] == "failed"
    assert result["payload"]["indeterminate"] is True
    message = result["payload"]["message"]
    assert "KHÔNG XÁC ĐỊNH" in message
    assert "Đồng bộ mã vận đơn" in message


def test_reconcile_survives_kiotviet_read_error():
    """Hoá đơn đọc không được cũng không được nổ ra ngoài — job phải kết thúc có thông điệp."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": False, "indeterminate": True, "error": "Read timed out."},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code", side_effect=requests.exceptions.ReadTimeout("nope")),
        patch(f"{_ACTIONS}.time.sleep"),
    ):
        result = _run_action(order)

    order.refresh_from_db()
    assert order.shipping_code == ""
    assert result["status"] == "failed"
    assert result["payload"]["indeterminate"] is True


def test_business_failure_does_not_trigger_reconcile():
    """Lỗi nghiệp vụ giữ nguyên thông điệp cũ của KShip, không đọc lại hoá đơn."""
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": False, "error": "Sai địa chỉ nhận"},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code") as get_invoice,
    ):
        result = _run_action(order)

    assert get_invoice.call_count == 0
    assert result["status"] == "failed"
    assert result["payload"]["message"] == "Sai địa chỉ nhận"
    assert result["payload"].get("indeterminate") is not True


def test_reconcile_does_not_run_when_create_order_succeeds():
    order = _order(_kiotviet_channel())
    with (
        patch(f"{_ACTIONS}.create_kship_delivery_for_invoice", return_value=_delivery_step1()),
        patch(f"{_ACTIONS}.build_kship_create_order_payload", return_value={}),
        patch(
            f"{_ACTIONS}.create_kship_order",
            return_value={"success": True, "order_number": "GHN1234", "total_fee": 30000},
        ),
        patch(f"{_ACTIONS}.get_invoice_by_code") as get_invoice,
    ):
        result = _run_action(order)

    order.refresh_from_db()
    assert get_invoice.call_count == 0
    assert order.shipping_code == "GHN1234"
    assert result["status"] == "created"


# --------------------------------------------------------------------------------------
# sync_shipping: đường dò TAY phải bóc mã y hệt đường dò TỰ ĐỘNG
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("invoice_result", "expected"),
    [
        ({"invoiceDelivery": {"deliveryCode": "GHN1"}}, "GHN1"),
        # KiotViet có endpoint trả deliveryCode phẳng ở gốc, không lồng invoiceDelivery.
        ({"deliveryCode": "GHN2"}, "GHN2"),
        ({"invoiceDelivery": {}}, ""),
        ({}, ""),
        (None, ""),
    ],
)
def test_sync_shipping_reads_delivery_code_same_way_as_reconcile(invoice_result, expected):
    order = _order(_kiotviet_channel())
    with patch(f"{_ACTIONS}.get_invoice_by_code", return_value=invoice_result):
        result = KiotVietService.execute_action(order, KiotVietService.ACTION_SYNC_SHIPPING)

    order.refresh_from_db()
    assert order.shipping_code == expected
    assert result["payload"]["shipping_code"] == expected
