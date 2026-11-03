"""Test entry tự động vào flow tác nghiệp (spec §4.4): at-ingest + sweep."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.contact_flow.application.services.entry_service import OperationEntryService
from apps.contact_flow.models import CustomerOperationState, OperationStage
from apps.customers.models import Customer
from apps.orders.models import Order, OrderSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def channel():
    return Channel.objects.create(code="entry-ch", name="Entry Channel", owner="Entry")


@pytest.fixture
def start_stage():
    return OperationStage.objects.create(name="Gọi lần đầu", is_start=True)


def _make_customer(phone="0911111111"):
    return Customer.objects.create(full_name="KH Entry", phone_raw=phone, phone_normalized=phone)


def _make_order(channel, customer, **overrides):
    defaults = dict(channel=channel, customer=customer, order_source=OrderSource.LANDING)
    defaults.update(overrides)
    return Order.objects.create(**defaults)


# (a) đơn landing mới → khách có state ở bước start
def test_enter_on_new_order_creates_state_for_landing_order(channel, start_stage):
    customer = _make_customer()
    order = _make_order(channel, customer)

    created = OperationEntryService.enter_on_new_order(order)

    assert created is True
    state = CustomerOperationState.objects.get(customer=customer)
    assert state.current_stage_id == start_stage.id


# (b) đơn order_source thuộc MARKETPLACE_ORDER_SOURCES/koc → không tạo
@pytest.mark.parametrize("source", [OrderSource.SHOPEE, OrderSource.TIKTOK, "koc"])
def test_enter_on_new_order_skips_marketplace_and_koc(channel, start_stage, source):
    customer = _make_customer(phone="0911111112")
    order = _make_order(channel, customer, order_source=source)

    created = OperationEntryService.enter_on_new_order(order)

    assert created is False
    assert not CustomerOperationState.objects.filter(customer=customer).exists()


# (c) khách đã có state → không đổi
def test_enter_on_new_order_noop_when_state_exists(channel, start_stage):
    customer = _make_customer(phone="0911111113")
    other_stage = OperationStage.objects.create(name="KNM 1")
    existing = CustomerOperationState.objects.create(
        customer=customer, current_stage=other_stage, entered_at=timezone.now()
    )
    order = _make_order(channel, customer)

    OperationEntryService.enter_on_new_order(order)

    existing.refresh_from_db()
    assert existing.current_stage_id == other_stage.id
    assert CustomerOperationState.objects.filter(customer=customer).count() == 1


# (d) sweep tạo state cho khách có đơn 24h chưa có state, bỏ qua đơn cũ hơn
def test_sweep_creates_state_for_recent_orders_and_skips_older(channel, start_stage):
    recent_customer = _make_customer(phone="0911111114")
    _make_order(channel, recent_customer)

    old_customer = _make_customer(phone="0911111115")
    old_order = _make_order(channel, old_customer)
    Order.objects.filter(pk=old_order.pk).update(
        created_at=timezone.now() - timedelta(hours=48)
    )

    summary = OperationEntryService.sweep(hours=24)

    assert summary == {"created": 1}
    assert CustomerOperationState.objects.filter(customer=recent_customer).exists()
    assert not CustomerOperationState.objects.filter(customer=old_customer).exists()


# (e) chưa cấu hình start → no-op không raise
def test_enter_on_new_order_noop_without_start_stage(channel):
    customer = _make_customer(phone="0911111116")
    order = _make_order(channel, customer)

    created = OperationEntryService.enter_on_new_order(order)

    assert created is False
    assert not CustomerOperationState.objects.filter(customer=customer).exists()


def test_sweep_noop_without_start_stage(channel):
    customer = _make_customer(phone="0911111117")
    _make_order(channel, customer)

    summary = OperationEntryService.sweep(hours=24)

    # Sau audit 2026-08-19: sweep nạp start stage MỘT lần đầu lô — thiếu stage
    # thì skip tường minh (kèm lý do) thay vì lặp N khách với kết quả None.
    assert summary == {"created": 0, "skipped": "no_start_stage"}
    assert not CustomerOperationState.objects.filter(customer=customer).exists()
