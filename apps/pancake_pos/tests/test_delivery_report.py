"""Báo cáo vận chuyển Pancake (MR D): đơn treo theo kho, lệch KiotViet ↔ Pancake, cảnh báo
Lark một lần/ngày đúng giờ; API chi tiết đơn / đơn treo / lệch (quyền); lệnh backfill."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment
from apps.pancake_pos.application.services import delivery_jobs
from apps.pancake_pos.application.services.delivery_report_service import DeliveryReportService, StuckConfig
from apps.pancake_pos.infrastructure.delivery_report_reader import SqlDeliveryReportReader
from apps.pancake_pos.models import (
    InboundMatchStatus,
    OrderLinkStatus,
    PancakePosInboundOrder,
    PancakePosOrderLink,
)
from apps.pancake_pos.tests.test_inbound_domain import order_payload
from apps.users.tests.helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)  # 09:00 giờ VN
_seq = itertools.count(1)


@pytest.fixture
def channel():
    return Channel.objects.create(code="DLX-REP", name="DLX-REP")


def crm_order(channel, **kw) -> Order:
    n = next(_seq)
    customer = Customer.objects.create(full_name=f"R{n}", phone_raw=f"09400{n:05d}",
                                       phone_normalized=f"09400{n:05d}", code=f"C-RP-{n}")
    code = kw.pop("code", None)
    order = Order.objects.create(channel=channel, customer=customer, name=f"R{n}", phone=f"09400{n:05d}",
                                 total_amount=1, total_after_discount=1, **{"status": 1, **kw})
    if code is not None:
        OrderAssessment.objects.create(order=order, delivery_business_status_code=code)
    return order


def inbound(pid: str, status: int, order=None, **kw) -> PancakePosInboundOrder:
    kw.setdefault("match_status", InboundMatchStatus.MATCHED if order else InboundMatchStatus.WAITING)
    return PancakePosInboundOrder.objects.create(pancake_order_id=pid, order=order, pancake_status=status, **kw)


def report_service(**cfg) -> DeliveryReportService:
    return DeliveryReportService(reader=SqlDeliveryReportReader(), config=StuckConfig(**cfg), status_names={},
                                 clock=lambda: NOW)


def test_don_treo_theo_kho(channel):
    order = crm_order(channel, order_code="DH-1")
    inbound("N-OLD", 12, order, inserted_at=NOW - timedelta(hours=30), warehouse_name="Đại Mỗ 1", status_name="Chờ in")
    inbound("N-NEW", 12, None, inserted_at=NOW - timedelta(hours=2), warehouse_name="Đại Mỗ 1")
    inbound("N-ANCIENT", 0, None, inserted_at=NOW - timedelta(days=45))  # ngoài cửa sổ 30 ngày
    inbound("N-WAIT", 17, None, inserted_at=NOW - timedelta(hours=40))  # Chờ xác nhận không tính
    inbound("N-IGN", 1, None, inserted_at=NOW - timedelta(hours=40), match_status="ignored")
    inbound("S-OLD", 2, None, shipped_at=NOW - timedelta(days=6), warehouse_name="Kho 2", dvvc_status="Đang giao hàng")
    inbound("S-NEW", 2, None, shipped_at=NOW - timedelta(days=2))
    inbound("S-DONE", 3, None, shipped_at=NOW - timedelta(days=9))
    rep = report_service().stuck()
    assert rep["not_sent"]["count"] == 1
    item = rep["not_sent"]["items"][0]
    assert (item["pancake_order_id"], item["order_id"], item["order_code"], item["age_hours"]) == (
        "N-OLD", order.id, "DH-1", 30.0)
    assert rep["not_sent"]["by_warehouse"] == [{"warehouse_name": "Đại Mỗ 1", "count": 1}]
    assert [i["pancake_order_id"] for i in rep["not_delivered"]["items"]] == ["S-OLD"]
    assert rep["not_delivered"]["items"][0]["age_hours"] == 144.0
    # Ngưỡng cấu hình được.
    assert report_service(not_delivered_days=1).stuck()["not_delivered"]["count"] == 2


def test_canh_bao_lark_mot_lan_dung_gio(channel):
    inbound("N-1", 8, None, inserted_at=NOW - timedelta(hours=30), warehouse_name="Đại Mỗ 1")
    sent = []

    def alert(**kw):
        sent.append(kw)
        return True

    assert report_service(alert_hour=9).alert_stuck(alert) is True
    assert sent[0]["key"] == "pancake_pos:stuck:2026-10-01" and sent[0]["dedupe_seconds"] > 3600
    assert "Đại Mỗ 1: 1" in "\n".join(sent[0]["lines"])
    assert report_service(alert_hour=10).alert_stuck(alert) is False  # chưa tới giờ
    assert report_service(alert_hour=-1).alert_stuck(alert) is False  # tắt
    PancakePosInboundOrder.objects.all().delete()
    assert report_service(alert_hour=9).alert_stuck(alert) is False  # không có đơn treo → im lặng
    assert len(sent) == 1


def test_lech_kiotviet_pancake(channel):
    cancelled = crm_order(channel, status=6, partner_delivery_status="Đã gửi hàng", order_code="DH-C")
    inbound("M-1", 2, cancelled)
    delivered = crm_order(channel, status=3, partner_delivery_status="Đang hoàn", code="dispatched")
    inbound("M-2", 4, delivered)
    not_dispatched = crm_order(channel, status=2, partner_delivery_status="Đã gửi hàng", code="warehouse_processing")
    inbound("M-3", 2, not_dispatched)
    fine = crm_order(channel, status=2, partner_delivery_status="Đã gửi hàng", code="dispatched")
    inbound("M-4", 2, fine)
    old = crm_order(channel, status=6, partner_delivery_status="Đã nhận")
    row = inbound("M-5", 3, old)
    PancakePosInboundOrder.objects.filter(id=row.id).update(updated_at=NOW - timedelta(days=40))
    rep = report_service().mismatches()
    rules = {i["order_id"]: i["rule"] for i in rep["items"]}
    assert rules == {
        cancelled.id: "crm_cancelled_pancake_shipped",
        delivered.id: "crm_delivered_pancake_returning",
        not_dispatched.id: "pancake_shipped_crm_not_dispatched",
    }
    item = next(i for i in rep["items"] if i["order_id"] == cancelled.id)
    assert (item["crm_status_label"], item["pancake_order_ids"], item["order_code"]) == ("Đã huỷ", ["M-1"], "DH-C")


# ── API ─────────────────────────────────────────────────────────────────────
def _client(codes) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=make_user_with_permissions(f"u{next(_seq)}", codes))
    return client


def test_api_chi_tiet_don_va_quyen(channel):
    order = crm_order(channel, partner_delivery_status="Đã gửi hàng", partner_dvvc_delivery_status="Đang giao hàng")
    inbound("D-1", 2, order, status_name="Đã gửi hàng", dvvc_status="Đang giao hàng", tracking_code="GHN1",
            shipped_at=NOW)
    inbound("D-2", 2, None)  # chưa khớp → không hiện
    res = _client(["orders:view"]).get(f"/api/pancake-pos/orders/{order.id}/delivery")
    assert res.status_code == 200
    assert res.data["partner_delivery_status"] == "Đã gửi hàng"
    assert [p["pancake_order_id"] for p in res.data["parts"]] == ["D-1"]
    assert res.data["parts"][0]["tracking_code"] == "GHN1"
    empty = _client(["orders:view"]).get("/api/pancake-pos/orders/999999/delivery")
    assert empty.status_code == 200 and empty.data["parts"] == []
    assert _client(["warehouses:view"]).get(f"/api/pancake-pos/orders/{order.id}/delivery").status_code == 403


def test_api_don_treo_va_lech(channel):
    inbound("A-1", 12, None, inserted_at=timezone.now() - timedelta(hours=48))
    viewer = _client(["pancake-pos:view"])
    res = viewer.get("/api/pancake-pos/delivery/stuck")
    assert res.status_code == 200 and res.data["not_sent"]["count"] == 1 and res.data["not_sent_hours"] == 24
    res = viewer.get("/api/pancake-pos/delivery/mismatches")
    assert res.status_code == 200 and res.data["count"] == 0 and res.data["window_days"] == 30
    outsider = _client(["orders:view"])
    assert outsider.get("/api/pancake-pos/delivery/stuck").status_code == 403
    assert outsider.get("/api/pancake-pos/delivery/mismatches").status_code == 403


# ── backfill ────────────────────────────────────────────────────────────────
class FakeClient:
    def __init__(self, orders):
        self.orders = orders
        self.calls = []

    def iter_orders_raw(self, *, since, until):
        self.calls.append((since, until))
        yield from self.orders


def test_backfill_qua_pipeline_khong_xuat_kho(channel, django_capture_on_commit_callbacks):
    order = crm_order(channel, code="")
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="B-1", status=OrderLinkStatus.PUSHED)
    pending = crm_order(channel)
    PancakePosOrderLink.objects.create(order=pending, pancake_order_id="B-2", status=OrderLinkStatus.PUSHED)
    client = FakeClient([
        order_payload(id="B-1", order_sources="-7", marketplace_id=None, status=2),
        order_payload(id="B-2", order_sources="-7", marketplace_id=None, status=12),
    ])
    with patch("apps.orders.application.services.order_service_impl.OrderService.dispatch_orders_batch") as dispatch, \
            patch("apps.pancake_pos.application.services.factory.dispatch_enabled", return_value=True), \
            django_capture_on_commit_callbacks(execute=True):
        report = delivery_jobs.backfill(days=3, dry_run=False, now=NOW, client=client)
    dispatch.assert_not_called()
    assert client.calls == [(int((NOW - timedelta(days=3)).timestamp()), int(NOW.timestamp()))]
    assert (report.fetched, report.stored, report.duplicate) == (2, 2, 0)
    assert report.delivery == {"blocked:needs_dispatch": 1, "written:warehouse_processing": 1}
    order.refresh_from_db()
    assert order.partner_delivery_status == "Đã gửi hàng"
    assert OrderAssessment.objects.get(order=pending).delivery_business_status_code == "warehouse_processing"
    # Chạy lại: payload trùng → bỏ.
    again = delivery_jobs.backfill(days=3, dry_run=False, now=NOW, client=client)
    assert (again.stored, again.duplicate) == (0, 2)


def test_backfill_dry_run_khong_ghi(channel):
    order = crm_order(channel)
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="B-3", status=OrderLinkStatus.PUSHED)
    client = FakeClient([order_payload(id="B-3", order_sources="-7", marketplace_id=None, status=3,
                                       partner={"partner_status": "delivered"})])
    report = delivery_jobs.backfill(days=1, dry_run=True, now=NOW, client=client)
    assert report.outcomes == {"match:order_link": 1}
    assert report.statuses == {"Đã nhận": 1} and report.dvvc == {"Đã giao hàng": 1}
    assert not PancakePosInboundOrder.objects.exists()
    order.refresh_from_db()
    assert order.partner_delivery_status == ""


def test_lenh_backfill_kiem_tham_so():
    with pytest.raises(CommandError):
        call_command("pancake_pos_backfill_delivery_status", "--days", "0")
    out = StringIO()
    with patch.object(delivery_jobs, "backfill", return_value=delivery_jobs.BackfillReport(fetched=3)) as run:
        call_command("pancake_pos_backfill_delivery_status", "--days", "7", "--dry-run", stdout=out)
    assert run.call_args.kwargs["days"] == 7 and run.call_args.kwargs["dry_run"] is True
    assert "[DRY-RUN] Đơn Pancake: 3" in out.getvalue()
