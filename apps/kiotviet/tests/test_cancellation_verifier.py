from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    InvoiceCancellationVerifier,
    VerificationUnavailable,
)
from apps.kiotviet.infrastructure.services.common_auth_customer import (
    KiotVietApiError,
    KiotVietInvoiceNotFound,
)
from apps.kiotviet.models import KiotVietChannel
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db

_PATCH_TARGET = "apps.kiotviet.application.services.cancellation_verifier.get_invoice_by_code"
_CACHE_TARGET = "apps.kiotviet.application.services.cancellation_verifier.cache"
_PUSH_TARGET = "apps.orders.application.services.order_service_impl.OrderService.push_event"
_BUS_TARGET = "apps.common.events.event_bus.publish_on_commit"


def _channel() -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        name=f"ch-{uuid.uuid4().hex[:8]}",
        client_id="cid",
        client_secret="secret",
        retailer="retailer",
    )


def _sales_channel() -> Channel:
    return Channel.objects.create(
        code=f"sc-{uuid.uuid4().hex[:8]}",
        name="Sales channel",
    )


def _customer() -> Customer:
    phone = f"09{uuid.uuid4().int % 10**8:08d}"
    return Customer.objects.create(
        full_name="KH test",
        phone_raw=phone,
        phone_normalized=phone,
    )


def _order(**kwargs) -> Order:
    defaults = {
        "channel": _sales_channel(),
        "customer": _customer(),
        "order_code": f"ORD-{uuid.uuid4().hex[:10]}",
        "invoice_code": f"HD-{uuid.uuid4().hex[:10]}",
        "status": OrderStatus.IN_DELIVERY,
        "name": "KH",
        "phone": "0900000001",
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def test_returns_ok_when_kiotviet_says_invoice_is_live():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, return_value={"status": 1}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
    assert api.call_count == 1


def test_returns_cancelled_and_heals_db_when_kiotviet_says_status_2():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, return_value={"status": 2}):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.CANCELLED)
    assert order.invoice_status == 2


def test_delivery_status_5_blocks_scan_and_heals_as_returned_not_cancelled():
    """delivery status 5 = đơn bị TRẢ/HOÀN, hóa đơn vẫn sống → không được ghi CANCELLED."""
    order = _order(kiotviet_channel=_channel(), invoice_status=1)
    with patch(_PATCH_TARGET, return_value={"status": 1, "invoiceDelivery": {"status": 5}}):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.RETURNED)
    assert order.invoice_status == 1  # KHÔNG bị ép thành 2


def test_delivery_status_6_with_live_invoice_blocks_scan_without_healing():
    """delivery status 6 = VẬN ĐƠN bị hủy nhưng hóa đơn còn sống (hủy giao để giao lại
    bằng vận đơn khác) → chặn quét, nhưng KHÔNG được lật đơn thành CANCELLED trong DB.
    Webhook cũng chặn đúng ca này (verify hóa đơn sống → skip) — hai lớp phải nhất quán."""
    order = _order(kiotviet_channel=_channel(), invoice_status=1)
    with (
        patch(_PATCH_TARGET, return_value={"status": 1, "invoiceDelivery": {"status": 6}}),
        patch(_BUS_TARGET) as publish,
    ):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.IN_DELIVERY)  # không heal
    assert order.invoice_status == 1
    for call in publish.call_args_list:
        assert not isinstance(call.args[0], OrderStatusChanged)


def test_delivery_status_6_verdict_is_cached_like_other_cancelled_verdicts():
    order = _order(kiotviet_channel=_channel(), invoice_status=1)
    with patch(_PATCH_TARGET, return_value={"status": 1, "invoiceDelivery": {"status": 6}}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    assert api.call_count == 1


def test_heals_invoice_status_when_order_already_cancelled(caplog):
    """Đơn đã heal một nửa (status=6, invoice_status!=2) → hoàn tất, không raise contract error."""
    order = _order(kiotviet_channel=_channel(), status=OrderStatus.CANCELLED, invoice_status=1)
    with patch(_PATCH_TARGET, return_value={"status": 2}):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.CANCELLED)
    assert order.invoice_status == 2


def test_returns_cancelled_even_when_push_event_fails():
    order = _order(kiotviet_channel=_channel())
    with (
        patch(_PATCH_TARGET, return_value={"status": 2}),
        patch(_PUSH_TARGET, side_effect=RuntimeError("redis down")) as push,
    ):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    push.assert_called_once()
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.CANCELLED)
    assert order.invoice_status == 2


def test_heal_to_cancelled_publishes_order_status_changed():
    """Heal 2 → 6 phải publish OrderStatusChanged: subscriber inventory dựa vào event này để
    release tồn giữ. Verifier heal trước webhook → webhook sẽ không publish nữa."""
    order = _order(kiotviet_channel=_channel(), status=OrderStatus.IN_DELIVERY)
    with (
        patch(_PATCH_TARGET, return_value={"status": 2}),
        patch(_BUS_TARGET) as publish,
    ):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED

    published = [call.args[0] for call in publish.call_args_list if isinstance(call.args[0], OrderStatusChanged)]
    assert published == [
        OrderStatusChanged(
            order_id=order.id,
            old_status=int(OrderStatus.IN_DELIVERY),
            new_status=int(OrderStatus.CANCELLED),
            source=StatusChangeSource.KIOTVIET_SYNC,
        )
    ]


def test_heal_delivery_status_5_publishes_order_status_changed():
    order = _order(kiotviet_channel=_channel(), status=OrderStatus.IN_DELIVERY, invoice_status=1)
    with (
        patch(_PATCH_TARGET, return_value={"status": 1, "invoiceDelivery": {"status": 5}}),
        patch(_BUS_TARGET) as publish,
    ):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED

    published = [call.args[0] for call in publish.call_args_list if isinstance(call.args[0], OrderStatusChanged)]
    assert published == [
        OrderStatusChanged(
            order_id=order.id,
            old_status=int(OrderStatus.IN_DELIVERY),
            new_status=int(OrderStatus.RETURNED),
            source=StatusChangeSource.KIOTVIET_SYNC,
        )
    ]


def test_no_status_change_does_not_publish_order_status_changed():
    """Đơn đã CANCELLED sẵn, chỉ vá invoice_status → không có transition → không publish."""
    order = _order(kiotviet_channel=_channel(), status=OrderStatus.CANCELLED, invoice_status=1)
    with (
        patch(_PATCH_TARGET, return_value={"status": 2}),
        patch(_BUS_TARGET) as publish,
    ):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED

    for call in publish.call_args_list:
        assert not isinstance(call.args[0], OrderStatusChanged)


def test_raises_when_payload_has_no_readable_status():
    """HTTP 200 nhưng body không đọc được trạng thái (vd {"raw_response": ...}) → fail-closed."""
    order = _order(kiotviet_channel=_channel())
    with patch(_CACHE_TARGET) as fake_cache:
        fake_cache.get.return_value = None
        with patch(_PATCH_TARGET, return_value={"raw_response": "<html>maintenance</html>"}):
            with pytest.raises(VerificationUnavailable):
                InvoiceCancellationVerifier.verify_not_cancelled(order)
        fake_cache.set.assert_not_called()  # không được cache một verdict chưa xác minh được
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.IN_DELIVERY)  # không heal gì cả


def test_returns_unverifiable_when_order_has_no_kiotviet_channel():
    order = _order(kiotviet_channel=None)
    with patch(_PATCH_TARGET) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.UNVERIFIABLE
    api.assert_not_called()


def test_returns_unverifiable_when_order_has_no_invoice_code():
    order = _order(kiotviet_channel=_channel(), invoice_code="")
    with patch(_PATCH_TARGET) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.UNVERIFIABLE
    api.assert_not_called()


def test_returns_unverifiable_when_invoice_not_found_on_channel():
    """404 = câu trả lời dứt khoát, không phải sự cố → fallback DB, KHÔNG chặn vĩnh viễn."""
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=KiotVietInvoiceNotFound("[stage=get_invoice] ... 404 - {}")):
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.UNVERIFIABLE
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.IN_DELIVERY)  # không heal gì cả


def test_raises_when_kiotviet_api_returns_server_error():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=KiotVietApiError("boom 500", status_code=500)):
        with pytest.raises(VerificationUnavailable):
            InvoiceCancellationVerifier.verify_not_cancelled(order)


def test_raises_when_kiotviet_api_times_out():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=TimeoutError("timed out")):
        with pytest.raises(VerificationUnavailable):
            InvoiceCancellationVerifier.verify_not_cancelled(order)


def test_api_error_is_not_cached_but_ok_result_is():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=Exception("boom")):
        with pytest.raises(VerificationUnavailable):
            InvoiceCancellationVerifier.verify_not_cancelled(order)
    # Lỗi không được cache → lần sau vẫn gọi API thật và trả OK.
    with patch(_PATCH_TARGET, return_value={"status": 1}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
    assert api.call_count == 1  # lần 2 lấy từ cache


def test_cancelled_result_is_cached():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, return_value={"status": 2}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.CANCELLED
    assert api.call_count == 1


def test_cache_ttl_is_300_for_ok_and_300_for_cancelled():
    """TTL OK = 300s: phủ cửa sổ "quét từng kiện → bấm Xác nhận" (30s cũ làm lô 50 kiện
    gọi lại KiotViet 50 lần tuần tự lúc bấm Xác nhận)."""
    order_ok = _order(kiotviet_channel=_channel())
    order_cancelled = _order(kiotviet_channel=_channel())

    with patch(_CACHE_TARGET) as fake_cache:
        fake_cache.get.return_value = None
        with patch(_PATCH_TARGET, return_value={"status": 1}):
            InvoiceCancellationVerifier.verify_not_cancelled(order_ok)
        ok_key, ok_value, ok_ttl = fake_cache.set.call_args[0]
        assert ok_value == CancelCheck.OK.value
        assert ok_ttl == 300

        fake_cache.set.reset_mock()
        with patch(_PATCH_TARGET, return_value={"status": 2}):
            InvoiceCancellationVerifier.verify_not_cancelled(order_cancelled)
        _, cancelled_value, cancelled_ttl = fake_cache.set.call_args[0]
        assert cancelled_value == CancelCheck.CANCELLED.value
        assert cancelled_ttl == 300


def test_garbage_cache_value_is_treated_as_miss():
    order = _order(kiotviet_channel=_channel())
    with patch(_CACHE_TARGET) as fake_cache:
        fake_cache.get.return_value = "not-a-verdict"
        with patch(_PATCH_TARGET, return_value={"status": 1}) as api:
            assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
        api.assert_called_once()


@override_settings(SCAN_VERIFY_CANCEL_REALTIME=False)
def test_kill_switch_skips_api_entirely():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.UNVERIFIABLE
    api.assert_not_called()
