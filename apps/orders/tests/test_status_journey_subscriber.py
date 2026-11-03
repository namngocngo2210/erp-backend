"""Mỗi lần `Order.status` đổi phải để lại vết trong `order_journey_events`.

Sinh ra từ điều tra 2026-07-23 (docs/erp-revive-stock-safety-net.md): 2 đơn bị huỷ rồi hồi
sinh mà tồn không được trừ lại, và KHÔNG truy được đường nào lật trạng thái — journey chỉ ghi
mã CRM, còn dấu vết duy nhất của `Order.status` là dòng log đã mất theo container.
"""
from __future__ import annotations

import pytest

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order, OrderJourneyEvent, OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ensure_orders_subscribers():
    """Đăng ký lại subscriber production trước mỗi test.

    Bus là singleton toàn process và vài app khác gọi `event_bus.clear()` trong teardown
    → handler đăng ký lúc `ready()` biến mất tuỳ THỨ TỰ chạy (chạy riêng thì pass, chạy
    cả suite thì fail). `register()` idempotent nên gọi lại ở đây là an toàn. Cùng pattern
    với `apps/warranty/tests/conftest.py`.
    """
    from apps.orders import subscribers

    subscribers.register()
    yield


@pytest.fixture
def order():
    channel = Channel.objects.create(code="journey-status", name="Journey Status")
    customer = Customer.objects.create(
        full_name="KH Journey", phone_raw="0909000111", phone_normalized="0909000111"
    )
    return Order.objects.create(
        channel=channel,
        customer=customer,
        name="KH Journey",
        phone="0909000111",
        total_amount="100000",
        status=OrderStatus.AWAITING_SHIPMENT,
    )


def _status_events(order: Order):
    return list(
        OrderJourneyEvent.objects.filter(
            order=order, event_type="order_status_changed"
        ).order_by("id")
    )


def test_status_change_writes_journey_event(order):
    event_bus.publish(
        OrderStatusChanged(
            order_id=order.id,
            old_status=int(OrderStatus.CANCELLED),
            new_status=int(OrderStatus.IN_DELIVERY),
        )
    )
    events = _status_events(order)
    assert len(events) == 1
    assert events[0].from_code == "6"
    assert events[0].to_code == "2"
    # Nhãn tiếng Việt trong payload để đọc journey không phải tra bảng số.
    assert events[0].payload_json["from_label"] == OrderStatus(6).label
    assert events[0].payload_json["to_label"] == OrderStatus(2).label


def test_revive_transition_is_recorded(order):
    """Đúng cú lật đã mù trong sự cố: 6 → 2 phải tra được bằng một query."""
    event_bus.publish(
        OrderStatusChanged(order_id=order.id, old_status=6, new_status=2)
    )
    assert _status_events(order)[0].from_code == "6"


def test_unknown_order_does_not_raise(order):
    """Đơn bị xoá cứng giữa lúc phát sự kiện → không được làm hỏng handler khác."""
    event_bus.publish(OrderStatusChanged(order_id=order.id + 999_999, old_status=6, new_status=2))
    assert OrderJourneyEvent.objects.filter(event_type="order_status_changed").count() == 0


def test_no_event_when_status_unchanged(order):
    """Publisher lỡ phát sự kiện không đổi trạng thái → không ghi rác vào journey."""
    event_bus.publish(OrderStatusChanged(order_id=order.id, old_status=2, new_status=2))
    assert _status_events(order) == []


def test_actor_and_source_are_recorded(order, django_user_model):
    """Thao tác do user gây ra (đẩy KiotViet, sửa tay) phải hiện tên họ, không phải "Hệ thống"."""
    user = django_user_model.objects.create_user(username="journey-actor", password="x")
    event_bus.publish(
        OrderStatusChanged(
            order_id=order.id,
            old_status=int(OrderStatus.NEW),
            new_status=int(OrderStatus.PENDING_PROCESSING),
            actor_id=user.id,
            source=StatusChangeSource.KIOTVIET_ACTION,
        )
    )
    [event] = _status_events(order)
    assert event.actor_id == user.id
    assert event.payload_json["source"] == "kiotviet_action"


def test_system_change_has_no_actor_but_keeps_source(order):
    event_bus.publish(
        OrderStatusChanged(
            order_id=order.id,
            old_status=int(OrderStatus.AWAITING_SHIPMENT),
            new_status=int(OrderStatus.IN_DELIVERY),
            source=StatusChangeSource.KIOTVIET_WEBHOOK,
        )
    )
    [event] = _status_events(order)
    assert event.actor_id is None
    assert event.payload_json["source"] == "kiotviet_webhook"
