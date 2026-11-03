"""Quy định xem hàng (ShipperNote) đi từ API tạo vận đơn vào OrderKiotVietJob và
tới execute_action.

Bảng mã nằm ở apps.kiotviet (xem test_gateway_kship_invoice_lookup.py); phía orders
chỉ chịu trách nhiệm chuyển đúng lựa chọn của TỪNG đơn xuống job, không phán xét mã.
"""
from __future__ import annotations

import itertools
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderKiotVietJob
from apps.users.tests.helpers import make_admin

pytestmark = pytest.mark.django_db

DELIVERY_URL = "/api/orders/kship-delivery"

# Test đa đơn: hai khách dùng chung số điện thoại sẽ đụng `cust_phone_norm_active_uniq`.
_PHONE_SEQ = itertools.count(1)


def _make_kiotviet_channel() -> KiotVietChannel:
    suffix = uuid.uuid4().hex[:8]
    return KiotVietChannel.objects.create(
        name=f"kv-{suffix}", retailer=f"r-{suffix}", old=False,
        username="u", password="p",
    )


def _make_order(kv: KiotVietChannel | None = None) -> Order:
    """Nhiều đơn trong một request PHẢI chung kênh KiotViet — view trả 400 nếu lệch."""
    suffix = uuid.uuid4().hex[:8]
    phone = f"09{next(_PHONE_SEQ):08d}"
    channel = Channel.objects.create(code=f"kship-{suffix}", name=f"KShip {suffix}")
    kv = kv or _make_kiotviet_channel()
    customer = Customer.objects.create(
        full_name=f"KH {suffix}",
        phone_raw=phone,
        phone_normalized=phone,
    )
    return Order.objects.create(
        channel=channel,
        customer=customer,
        order_code=f"ORD-{suffix}",
        name="KH",
        phone=phone,
        total_amount="0",
        kiotviet_channel=kv,
        invoice_code=f"HD-{suffix}",
    )


def _admin_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=make_admin(f"kship-shipnote-{uuid.uuid4().hex[:6]}"))
    return client


def _job(order: Order) -> OrderKiotVietJob:
    return OrderKiotVietJob.objects.get(
        order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY
    )


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_shipper_note_saved_per_order(mock_dispatch):
    """Mỗi đơn giữ lựa chọn riêng — khác provider/service vốn dùng chung cả lô."""
    kv = _make_kiotviet_channel()
    first, second = _make_order(kv), _make_order(kv)
    response = _admin_client().post(
        DELIVERY_URL,
        {
            "ids": [first.id, second.id],
            "provider_code": "BEST",
            "service_code": "STD",
            "shipper_notes": {
                str(first.id): "KHONGCHOXEMHANG",
                str(second.id): "CHOTHUHANG",
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert _job(first).kship_shipper_note == "KHONGCHOXEMHANG"
    assert _job(second).kship_shipper_note == "CHOTHUHANG"


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_missing_key_leaves_job_blank(mock_dispatch):
    """Thiếu key cho một đơn thì để rỗng; payload builder tự rơi về mặc định."""
    kv = _make_kiotviet_channel()
    chosen, skipped = _make_order(kv), _make_order(kv)
    response = _admin_client().post(
        DELIVERY_URL,
        {
            "ids": [chosen.id, skipped.id],
            "provider_code": "BEST",
            "service_code": "STD",
            "shipper_notes": {str(chosen.id): "CHOTHUHANG"},
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert _job(skipped).kship_shipper_note == ""


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_request_without_shipper_notes_still_succeeds(mock_dispatch):
    """Tương thích ngược: client cũ không gửi field vẫn tạo được vận đơn."""
    order = _make_order()
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert _job(order).kship_shipper_note == ""


# Patch tại nơi class thật sự sống: ingest_job_tasks gọi KiotViet qua port chứ không
# import KiotVietService nữa; patch thuộc tính trên class vẫn chặn đúng call qua adapter.
@patch("apps.kiotviet.infrastructure.services.service_actions.KiotVietService.execute_action")
def test_task_forwards_shipper_note(mock_execute):
    from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
        process_kiotviet_job,
    )

    order = _make_order()
    job = OrderKiotVietJob.objects.create(
        order=order,
        action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY,
        kship_provider_code="BEST",
        kship_service_code="STD",
        kship_shipper_note="KHONGCHOXEMHANG",
    )
    mock_execute.return_value = {"status": "success", "payload": {}}
    process_kiotviet_job(job_id=job.id)
    assert mock_execute.call_args.kwargs.get("shipper_note") == "KHONGCHOXEMHANG"


# ---------------------------------------------------------------------------
# Lựa chọn SERVICE_EXTRA của hãng (hiện là ca lấy hàng SPX) — chung cả lô vì
# provider/service cũng gửi ở mức lô. orders chỉ chuyển tiếp; giá trị hợp lệ do
# gateway đối chiếu lại với KShip ngay lúc tạo vận đơn.
# ---------------------------------------------------------------------------


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_service_extra_saved_for_every_job_in_the_batch(mock_dispatch):
    kv = _make_kiotviet_channel()
    first, second = _make_order(kv), _make_order(kv)
    response = _admin_client().post(
        DELIVERY_URL,
        {
            "ids": [first.id, second.id],
            "provider_code": "SPX",
            "service_code": "1",
            "service_extra": {"PickShift": "1789030800-2"},
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert _job(first).kship_service_extra == {"PickShift": "1789030800-2"}
    assert _job(second).kship_service_extra == {"PickShift": "1789030800-2"}


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_request_without_service_extra_still_succeeds(mock_dispatch):
    """Tương thích ngược: client cũ không gửi field vẫn tạo được vận đơn."""
    order = _make_order()
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert _job(order).kship_service_extra == {}


@patch("apps.kiotviet.infrastructure.services.service_actions.KiotVietService.execute_action")
def test_task_forwards_service_extra(mock_execute):
    from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
        process_kiotviet_job,
    )

    order = _make_order()
    job = OrderKiotVietJob.objects.create(
        order=order,
        action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY,
        kship_provider_code="SPX",
        kship_service_code="1",
        kship_service_extra={"PickShift": "1789030800-2"},
    )
    mock_execute.return_value = {"status": "success", "payload": {}}
    process_kiotviet_job(job_id=job.id)
    assert mock_execute.call_args.kwargs.get("service_extra") == {"PickShift": "1789030800-2"}
