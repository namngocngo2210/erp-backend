"""Đồng bộ "Trạng thái vận chuyển thực" theo Pancake (MR D) trên DB thật: ghi qua đường sửa
đánh giá (journey source=pancake_pos), cờ xuất kho tắt/bật, xuất kho qua đúng
`dispatch_orders_batch`, không ghi đè mã người nhập, backfill không bao giờ xuất kho."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.pancake_pos.application.services.delivery_sync_service import DeliverySyncService
from apps.pancake_pos.infrastructure.crm_delivery_gateway import SqlCrmDeliveryGateway
from apps.pancake_pos.models import (
    InboundMatchStatus,
    PancakePosInboundOrder,
    PancakePosStatusMap,
    PancakePosWarehouseLink,
)
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

DISPATCH = "apps.orders.application.services.order_service_impl.OrderService.dispatch_orders_batch"
_seq = itertools.count(1)


@pytest.fixture
def channel():
    return Channel.objects.create(code="DLX-SYNC", name="DLX-SYNC")


def crm_order(channel, *, code: str | None = None, **kw) -> Order:
    n = next(_seq)
    customer = Customer.objects.create(full_name=f"S{n}", phone_raw=f"09300{n:05d}",
                                       phone_normalized=f"09300{n:05d}", code=f"C-SY-{n}")
    order = Order.objects.create(channel=channel, customer=customer, name=f"S{n}", phone=f"09300{n:05d}",
                                 status=kw.pop("status", 2), total_amount=1, total_after_discount=1, **kw)
    if code is not None:
        OrderAssessment.objects.create(order=order, delivery_business_status_code=code)
    return order


def part(order, pid: str, status: int, **kw) -> PancakePosInboundOrder:
    return PancakePosInboundOrder.objects.create(
        pancake_order_id=pid, order=order, match_status=InboundMatchStatus.MATCHED, pancake_status=status,
        inserted_at=kw.pop("inserted_at", datetime(2026, 9, 30, tzinfo=UTC)), **kw,
    )


def service(*, enabled: bool = False) -> DeliverySyncService:
    return DeliverySyncService(crm=SqlCrmDeliveryGateway(), dispatch_enabled=enabled)


def code_of(order) -> str:
    return OrderAssessment.objects.filter(order=order).values_list("delivery_business_status_code", flat=True).first() or ""


def test_seed_ma_vt_trong_status_map():
    codes = dict(PancakePosStatusMap.objects.values_list("pancake_status", "crm_delivery_code"))
    assert codes[12] == "warehouse_processing" and codes[2] == "dispatched" and codes[3] == "received"
    assert codes[4] == "not_received" and codes[6] == "" and codes[15] == ""


def test_chua_gui_ghi_kho_dang_xu_ly_qua_journey(channel):
    order = crm_order(channel, status=1)
    part(order, "S-1", 12)
    outcome = service().sync(order.id)
    assert (outcome.result, outcome.detail) == ("written", "warehouse_processing")
    assert code_of(order) == "warehouse_processing"
    ev = OrderJourneyEvent.objects.filter(order=order, event_type__in=[
        "delivery_business_status_changed", "assessment_created"]).latest("id")
    assert ev.actor_id is None and ev.payload_json.get("source") == "pancake_pos"
    # Chạy lại: không đổi gì.
    assert service().sync(order.id).result == "noop"


def test_co_tat_khong_xuat_kho_va_khong_dung_don_da_xuat(channel):
    order = crm_order(channel, code="warehouse_processing")
    part(order, "S-2", 2)
    with patch(DISPATCH) as dispatch:
        outcome = service().sync(order.id)
    assert (outcome.result, outcome.detail) == ("blocked", "needs_dispatch")
    dispatch.assert_not_called()
    assert code_of(order) == "warehouse_processing"
    done = crm_order(channel, code="dispatched")
    part(done, "S-3", 3)
    assert str(service().sync(done.id)) == "written:received"
    assert code_of(done) == "received"


def test_co_tat_ghi_thang_ma_hoan_khong_xuat_kho(channel):
    order = crm_order(channel, code="warehouse_processing")
    inbound = part(order, "S-10", 5)
    with patch(DISPATCH) as dispatch:
        assert str(service().sync(order.id)) == "written:not_received"
        dispatch.assert_not_called()
    assert code_of(order) == "not_received"
    # Pancake đặt mã → đổi ngang được (giao lại thành công).
    PancakePosInboundOrder.objects.filter(pk=inbound.pk).update(pancake_status=3)
    assert str(service().sync(order.id)) == "written:received"


def test_co_bat_xuat_kho_qua_dung_luong_quet_kho(channel):
    wh_pancake = Warehouse.objects.create(code="DM1", name="Đại Mỗ 1")
    PancakePosWarehouseLink.objects.create(warehouse=wh_pancake, pancake_warehouse_id="pw-1")
    order = crm_order(channel, code="warehouse_processing")
    shipped = datetime(2026, 9, 30, 9, tzinfo=UTC)
    part(order, "S-4", 2, pancake_warehouse_id="pw-1", shipped_at=shipped)
    with patch(DISPATCH, return_value=[{"order_id": order.id, "ok": True}]) as dispatch:
        outcome = service(enabled=True).sync(order.id)
    assert (outcome.result, outcome.detail) == ("dispatched", "dispatched")
    kwargs = dispatch.call_args.kwargs
    assert kwargs["order_ids"] == [order.id] and kwargs["warehouse_id"] == wh_pancake.id
    assert kwargs["occurred_at"] == shipped and kwargs["journey_source"] == "pancake_pos" and kwargs["actor"] is None


def test_co_bat_da_nhan_tu_truoc_xuat_kho_thi_xuat_roi_ghi(channel):
    wh = Warehouse.objects.create(code="DM2", name="Kho đơn")
    order = crm_order(channel, code="", fulfillment_warehouse=wh)
    part(order, "S-5", 3)

    def fake_dispatch(**kw):
        OrderAssessment.objects.filter(order_id=order.id).update(delivery_business_status_code="dispatched")
        return [{"order_id": order.id, "ok": True}]

    with patch(DISPATCH, side_effect=fake_dispatch) as dispatch:
        outcome = service(enabled=True).sync(order.id)
    assert dispatch.call_args.kwargs["warehouse_id"] == wh.id  # kho Pancake chưa ghép → kho của đơn
    assert (outcome.result, outcome.detail) == ("dispatched", "received")
    assert code_of(order) == "received"


def test_xuat_kho_loi_va_thieu_kho(channel):
    order = crm_order(channel, code="")
    part(order, "S-6", 2)
    assert str(service(enabled=True).sync(order.id)) == "blocked:no_warehouse"
    wh = Warehouse.objects.create(code="DM3", name="K3")
    Order.objects.filter(id=order.id).update(fulfillment_warehouse=wh)
    with patch(DISPATCH, return_value=[{"order_id": order.id, "ok": False, "error": "verify_unavailable"}]):
        assert str(service(enabled=True).sync(order.id)) == "error:verify_unavailable"
    assert code_of(order) == ""


def test_backfill_khong_bao_gio_xuat_kho(channel):
    order = crm_order(channel, code="")
    part(order, "S-7", 2)
    with patch(DISPATCH) as dispatch:
        assert str(service(enabled=True).sync(order.id, allow_dispatch=False)) == "blocked:needs_dispatch"
    dispatch.assert_not_called()


def test_khong_ghi_de_ma_nguoi_nhap_va_doi_ngang_khi_pancake_dat(channel):
    manual = crm_order(channel, code="stalled_complaint")
    part(manual, "S-8", 3)
    assert str(service(enabled=True).sync(manual.id)) == "blocked:manual_code"
    user_received = crm_order(channel, code="received")
    part(user_received, "S-9", 4)
    assert str(service(enabled=True).sync(user_received.id)) == "blocked:manual_code"
    # Mã do Pancake đặt (journey source=pancake_pos) → Đang hoàn đổi được sang Chưa nhận.
    owned = crm_order(channel, code="dispatched")
    row = part(owned, "S-10", 3)
    assert str(service(enabled=True).sync(owned.id)) == "written:received"
    row.pancake_status = 4
    row.save(update_fields=["pancake_status"])
    assert str(service(enabled=True).sync(owned.id)) == "written:not_received"
    assert code_of(owned) == "not_received"


def test_huy_va_khong_co_kien_thi_khong_lam_gi(channel):
    order = crm_order(channel, code="warehouse_processing")
    part(order, "S-11", 6)
    assert service().sync(order.id).result == "noop"
    lonely = crm_order(channel)
    assert str(service().sync(lonely.id)) == "noop:no_target"


def test_don_tach_kien_xuat_khi_moi_kien_da_gui(channel):
    order = crm_order(channel, code="warehouse_processing")
    part(order, "S-12", 2)
    part(order, "S-13", 13, inserted_at=datetime(2026, 9, 30, 1, tzinfo=UTC))
    # Kiện chậm nhất "Đã in" → giữ Kho đang xử lý, chưa xuất kho.
    with patch(DISPATCH) as dispatch:
        assert service(enabled=True).sync(order.id).result == "noop"
    dispatch.assert_not_called()
