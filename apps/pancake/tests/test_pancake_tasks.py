"""Task poll: gate bằng cờ, single-flight, chỉ page đã bật; task retry_lead_order chỉ
tạo lại đơn cho lead CHƯA có order và ở trạng thái chờ/lỗi. Route + beat được
test_celery_routes bảo vệ, ở đây chỉ chốt queue."""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from apps.channels_core.models import Channel
from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage, PancakePushStatus
from apps.pancake.tasks import poll_phone_leads, retry_lead_order

pytestmark = pytest.mark.django_db

POLL_SERVICE = "apps.pancake.tasks.PancakePollService"
CREATE_ORDER = "apps.pancake.tasks.create_order_for_lead"


def test_route_va_beat():
    assert settings.CELERY_TASK_ROUTES["apps.pancake.tasks.poll_phone_leads"]["queue"] == settings.CELERY_FOXIA_QUEUE
    assert settings.CELERY_TASK_ROUTES["apps.pancake.tasks.retry_lead_order"]["queue"] == settings.CELERY_FOXIA_PUSH_QUEUE
    assert settings.CELERY_BEAT_SCHEDULE["pancake-poll-phone-leads"]["task"] == "apps.pancake.tasks.poll_phone_leads"
    assert settings.CELERY_BEAT_SCHEDULE["pancake-poll-phone-leads"]["schedule"] == 180.0


@override_settings(PANCAKE_POLL_ENABLED=False)
def test_poll_tat_co_thi_disabled():
    assert poll_phone_leads() == "disabled"


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_chua_co_connection():
    assert poll_phone_leads() == "no_client"


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_chi_page_da_bat_va_gom_ket_qua():
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    on = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel, is_enabled=True, page_access_token="t")
    PancakePage.objects.create(pancake_page_id="2", platform="facebook", channel=channel, is_enabled=False, page_access_token="t")
    with patch(POLL_SERVICE) as service_cls:
        service_cls.return_value.poll_page.side_effect = lambda page: type("R", (), {"leads_created": 2, "error": ""})()
        assert poll_phone_leads() == "ok:1:2"
    assert [call.args[0].id for call in service_cls.return_value.poll_page.call_args_list] == [on.id]


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_single_flight():
    from django.core.cache import cache

    from apps.pancake.tasks import POLL_LOCK_KEY

    cache.add(POLL_LOCK_KEY, "khac", 60)
    try:
        assert poll_phone_leads() == "skipped"
    finally:
        cache.delete(POLL_LOCK_KEY)


def _lead(status, **overrides):
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", name="P", platform="facebook", channel=channel)
    values = dict(page=page, channel=channel, conversation_id="c", phone_raw="0901234567",
                  phone="0901234567", foxia_kind="messenger", push_status=status)
    values.update(overrides)
    return PancakeLead.objects.create(**values)


def test_retry_lead_order_chi_lead_chua_co_don():
    lead = _lead(PancakePushStatus.OK)
    with patch(CREATE_ORDER) as create:
        assert retry_lead_order(lead.id) == "not_eligible"
    create.assert_not_called()

    lead.push_status = PancakePushStatus.FAILED
    lead.save()
    with patch(CREATE_ORDER, side_effect=lambda l: l) as create:
        # Mock không đổi push_status thật (vẫn "failed") → task báo "failed".
        assert retry_lead_order(lead.id) == "failed"
    assert create.call_args.args[0].id == lead.id
    assert retry_lead_order(lead.id + 999) == "missing"


def test_retry_lead_order_order_failed_thi_created():
    lead = _lead(PancakePushStatus.ORDER_FAILED)

    def _mark_created(l):
        l.push_status = PancakePushStatus.ORDER_CREATED
        return l

    with patch(CREATE_ORDER, side_effect=_mark_created) as create:
        assert retry_lead_order(lead.id) == "created"
    assert create.call_args.args[0].id == lead.id


def test_retry_lead_order_order_exists_thi_exists():
    """Retry rơi vào `DuplicateOrderError` (trùng đơn trong ngày, gắn vào đơn có
    sẵn) → `create_order_for_lead` trả `push_status=order_exists` — task phải báo
    riêng "exists", không lẫn với "created" (đơn MỚI) hay "failed" (lỗi thật)."""
    lead = _lead(PancakePushStatus.ORDER_FAILED)

    def _mark_exists(l):
        l.push_status = PancakePushStatus.ORDER_EXISTS
        return l

    with patch(CREATE_ORDER, side_effect=_mark_exists) as create:
        assert retry_lead_order(lead.id) == "exists"
    assert create.call_args.args[0].id == lead.id


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_retry_lead_order_da_co_don_thi_not_eligible():
    # `order_id` là điều kiện chặn chính — lead có order rồi thì dù push_status
    # là gì cũng không tạo lại (tránh tạo trùng đơn).
    from apps.orders.application.services.order_service_impl import OrderService

    lead = _lead(PancakePushStatus.ORDER_FAILED)
    order = OrderService.create_order_from_landing(
        {"channel_id": lead.channel_id, "phone": lead.phone, "order_source": "messenger"}
    )
    lead.order = order
    lead.save(update_fields=["order"])
    with patch(CREATE_ORDER) as create:
        assert retry_lead_order(lead.id) == "not_eligible"
    create.assert_not_called()


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_retry_lead_order_order_exists_thi_not_eligible():
    """`order_exists` (lead trùng đơn trong ngày, gắn vào đơn có sẵn — xem
    lead_order_service) cũng có `order_id` → không tạo lại, tránh đơn thứ hai."""
    from apps.orders.application.services.order_service_impl import OrderService

    lead = _lead(PancakePushStatus.ORDER_EXISTS)
    order = OrderService.create_order_from_landing(
        {"channel_id": lead.channel_id, "phone": lead.phone, "order_source": "messenger"}
    )
    lead.order = order
    lead.save(update_fields=["order"])
    with patch(CREATE_ORDER) as create:
        assert retry_lead_order(lead.id) == "not_eligible"
    create.assert_not_called()


def test_retry_lead_order_pending_cung_tao_lai_duoc():
    """Worker bị giết giữa lúc tạo lead và create_order_for_lead để lại lead
    `pending` mãi mãi nếu tác vụ tạo lại đơn không nhận trạng thái này."""
    lead = _lead(PancakePushStatus.PENDING)

    def _mark_created(l):
        l.push_status = PancakePushStatus.ORDER_CREATED
        return l

    with patch(CREATE_ORDER, side_effect=_mark_created) as create:
        assert retry_lead_order(lead.id) == "created"
    assert create.call_args.args[0].id == lead.id
