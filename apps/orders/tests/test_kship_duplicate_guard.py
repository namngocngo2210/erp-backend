"""Đơn đã có mã vận đơn thì không được tạo vận đơn thứ hai.

KShip không có khoá chống trùng (`payload["UUID"]` luôn rỗng), nên bấm tạo lại trên
đơn đã có `shipping_code` sẽ sinh vận đơn THẬT thứ hai bên đơn vị vận chuyển: mất phí
thật, còn mã cũ thì mồ côi vì DB chỉ giữ được một mã. Cửa chặn phải nằm ở API — FE chỉ
là lớp ngoài, gọi thẳng API vẫn phải bị chặn.
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

_PHONE_SEQ = itertools.count(1)


def _make_kiotviet_channel() -> KiotVietChannel:
    suffix = uuid.uuid4().hex[:8]
    return KiotVietChannel.objects.create(
        name=f"kv-{suffix}", retailer=f"r-{suffix}", old=False, username="u", password="p",
    )


def _make_order(kv: KiotVietChannel | None = None, shipping_code: str = "") -> Order:
    suffix = uuid.uuid4().hex[:8]
    phone = f"09{next(_PHONE_SEQ):08d}"
    channel = Channel.objects.create(code=f"kshipdup-{suffix}", name=f"KShip {suffix}")
    kv = kv or _make_kiotviet_channel()
    customer = Customer.objects.create(
        full_name=f"KH {suffix}", phone_raw=phone, phone_normalized=phone,
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
        shipping_code=shipping_code,
    )


def _admin_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=make_admin(f"kship-dup-{uuid.uuid4().hex[:6]}"))
    return client


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_order_with_shipping_code_is_rejected(mock_dispatch):
    order = _make_order(shipping_code="GHN-EXISTING")
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert response.json()["orders_with_shipping_code"] == [order.id]
    assert not OrderKiotVietJob.objects.filter(order=order).exists()
    assert mock_dispatch.call_count == 0


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_whole_batch_is_rejected_when_one_order_already_has_code(mock_dispatch):
    """Chặn cả lô: tạo một phần rồi báo lỗi khiến user không biết đơn nào đã đi."""
    kv = _make_kiotviet_channel()
    clean = _make_order(kv)
    dirty = _make_order(kv, shipping_code="GHN-EXISTING")
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [clean.id, dirty.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert response.json()["orders_with_shipping_code"] == [dirty.id]
    assert not OrderKiotVietJob.objects.filter(order__in=[clean, dirty]).exists()


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_order_without_shipping_code_still_passes(mock_dispatch):
    order = _make_order()
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert OrderKiotVietJob.objects.filter(
        order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY
    ).exists()
