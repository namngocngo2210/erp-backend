"""Endpoint webhook Pancake (two-way-sync B1): secret, chống trùng, phân loại + xếp task,
giới hạn kích thước; API quản trị (trạng thái, cấu hình dry-run/ghi, xử lý lại lỗi, chờ
khớp, khớp tay) — quyền; lệnh `pancake_pos_webhook`."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from rest_framework.test import APIClient

from apps.pancake_pos.models import (
    PancakePosInboundOrder,
    PancakePosWebhookEvent,
    WebhookEventStatus,
)
from apps.pancake_pos.tests.test_inbound_domain import order_payload
from apps.sysconfig import override_sysconfig
from apps.users.tests.helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

URL = "/api/pancake-pos/webhook"
SECRET = "s3cret-webhook-0123456789"
HEADER = "HTTP_X_DESI_WEBHOOK_SECRET"
TASK = "apps.pancake_pos.tasks.process_webhook_event"
CONFIG = {"PANCAKE_POS_WEBHOOK_SECRET": SECRET, "PANCAKE_POS_WEBHOOK_URL": "https://crm.example.vn/api/pancake-pos/webhook"}


def _client(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


@pytest.fixture
def editor(db):
    return _client(make_user_with_permissions("wh_editor", ["pancake-pos:view", "pancake-pos:edit"]))


@pytest.fixture
def viewer(db):
    return _client(make_user_with_permissions("wh_viewer", ["pancake-pos:view"]))


@pytest.fixture
def outsider(db):
    return _client(make_user_with_permissions("wh_none", ["warehouses:view"]))


def post(payload, secret=SECRET, **extra):
    kwargs = {HEADER: secret} if secret is not None else {}
    return _client().post(URL, data=json.dumps(payload), content_type="application/json", **kwargs, **extra)


# ── nhận webhook ────────────────────────────────────────────────────────────
def test_chua_dat_secret_tra_503():
    with override_sysconfig(PANCAKE_POS_WEBHOOK_SECRET=""):
        assert post(order_payload()).status_code == 503
    assert PancakePosWebhookEvent.objects.count() == 0


def test_sai_hoac_thieu_secret_tra_401():
    with override_sysconfig(**CONFIG):
        assert post(order_payload(), secret="sai").status_code == 401
        assert post(order_payload(), secret=None).status_code == 401
    assert PancakePosWebhookEvent.objects.count() == 0


def test_luu_tho_chong_trung_va_xep_task(django_capture_on_commit_callbacks):
    with override_sysconfig(**CONFIG), patch(f"{TASK}.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            res = post(order_payload())
        assert res.status_code == 200 and res.data == {"ok": True, "stored": 1, "duplicate": 0}
        with django_capture_on_commit_callbacks(execute=True):
            again = post(order_payload())
        assert again.data == {"ok": True, "stored": 0, "duplicate": 1}
    ev = PancakePosWebhookEvent.objects.get()
    assert (ev.event_type, ev.record_id, ev.status) == ("orders", "260930BY9FBX6H", WebhookEventStatus.PENDING)
    delay.assert_called_once_with(ev.id)


def test_ton_va_san_pham_luu_pending_cho_b2_khong_xep_task(django_capture_on_commit_callbacks):
    inventory = {"data": {"record": {"variation_id": "v1", "warehouse_id": "w1", "change_quantity": -1,
                                     "inserted_at": "2026-09-30T01:00:00", "type": "order"}}}
    product = {"data": {"record": {"id": "p1", "name": "SP", "variations": []}}}
    with (
        override_sysconfig(**CONFIG),
        patch(f"{TASK}.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        assert post([inventory, product, {"foo": 1}]).data["stored"] == 3
    delay.assert_not_called()
    rows = dict(PancakePosWebhookEvent.objects.values_list("event_type", "status"))
    assert rows == {"variations_warehouses": "pending", "products": "pending", "unknown": "ignored"}


def test_payload_sai_va_qua_lon():
    with override_sysconfig(**CONFIG):
        assert post("chuoi").status_code == 400
        assert post({}).status_code == 400
        big = post(order_payload(), CONTENT_LENGTH=str(3 * 1024 * 1024))
        assert big.status_code == 413


# ── quản trị ────────────────────────────────────────────────────────────────
def test_trang_thai_webhook(viewer):
    PancakePosWebhookEvent.objects.create(event_type="orders", record_id="1", payload_hash="a", payload={},
                                          status="error", error="DB bận")
    PancakePosWebhookEvent.objects.create(event_type="orders", record_id="2", payload_hash="b", payload={})
    with override_sysconfig(**CONFIG):
        res = viewer.get("/api/pancake-pos/webhook/status")
    assert res.status_code == 200
    assert res.data["config"]["secret_set"] is True and res.data["config"]["header"] == "X-Desi-Webhook-Secret"
    assert {(c["status"], c["count"]) for c in res.data["counts_24h"]} == {("error", 1), ("pending", 1)}
    assert res.data["error_count"] == 1 and res.data["last_error"]["error"] == "DB bận"


def test_quyen_quan_tri(viewer, outsider):
    assert outsider.get("/api/pancake-pos/webhook/status").status_code == 403
    assert outsider.get("/api/pancake-pos/inbound-orders").status_code == 403
    assert viewer.post("/api/pancake-pos/webhook/configure", {"dry_run": True}, format="json").status_code == 403
    assert viewer.post("/api/pancake-pos/webhook/retry-errors").status_code == 403
    assert viewer.post("/api/pancake-pos/inbound-orders/1/resolve", {"action": "ignore"},
                       format="json").status_code == 403


def test_cau_hinh_dry_run_che_secret_va_khong_goi_pancake(editor):
    with override_sysconfig(**CONFIG), patch(
        "apps.pancake_pos.application.services.webhook_config.build_client"
    ) as build:
        res = editor.post("/api/pancake-pos/webhook/configure", {"dry_run": True}, format="json")
    assert res.status_code == 200
    shop = res.data["body"]["shop"]
    assert shop["webhook_url"] == CONFIG["PANCAKE_POS_WEBHOOK_URL"]
    assert shop["webhook_types"] == ["orders", "products", "variations_warehouses"]
    assert shop["webhook_headers"] == {"X-Desi-Webhook-Secret": "***6789"}
    assert SECRET not in json.dumps(res.data)
    build.assert_not_called()


def test_cau_hinh_loi_va_ghi_that(editor):
    with override_sysconfig(PANCAKE_POS_WEBHOOK_SECRET="ngan", PANCAKE_POS_WEBHOOK_URL="https://x"):
        assert editor.post("/api/pancake-pos/webhook/configure", {"dry_run": True}, format="json").status_code == 400
    with override_sysconfig(**{**CONFIG, "PANCAKE_POS_WEBHOOK_URL": "http://x"}):
        assert editor.post("/api/pancake-pos/webhook/configure", {"dry_run": True}, format="json").status_code == 400
    with override_sysconfig(**CONFIG, PANCAKE_POS_ENABLED=False):
        assert editor.post("/api/pancake-pos/webhook/configure", {"dry_run": False}, format="json").status_code == 409
    client = MagicMock()
    client.update_webhook.return_value = {"webhook_enable": True, "webhook_headers": {"X-Desi-Webhook-Secret": SECRET}}
    with override_sysconfig(**CONFIG, PANCAKE_POS_ENABLED=True), patch(
        "apps.pancake_pos.application.services.webhook_config.build_client", return_value=client
    ):
        res = editor.post("/api/pancake-pos/webhook/configure", {"dry_run": False}, format="json")
    assert res.status_code == 200
    sent = client.update_webhook.call_args.args[0]
    assert sent["webhook_headers"] == {"X-Desi-Webhook-Secret": SECRET} and sent["webhook_enable"] is True
    assert SECRET not in json.dumps(res.data)


def test_client_put_shop():
    from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosClient

    http = MagicMock()
    http.request.return_value = MagicMock(status_code=200, json=lambda: {"success": True, "shop": {"webhook_enable": True}})
    client = PancakePosClient("key", 42, http=http, sleep=lambda _s: None)
    assert client.update_webhook({"webhook_enable": True}) == {"webhook_enable": True}
    method, path = http.request.call_args.args
    assert (method, path) == ("PUT", "/shops/42")
    assert http.request.call_args.kwargs["json"] == {"shop": {"webhook_enable": True}}


def test_xu_ly_lai_su_kien_loi(editor):
    ev = PancakePosWebhookEvent.objects.create(event_type="orders", record_id="1", payload_hash="a", payload={},
                                               status="error", error="x", attempts=5)
    with patch(f"{TASK}.delay") as delay:
        res = editor.post("/api/pancake-pos/webhook/retry-errors")
    assert res.status_code == 202 and res.data == {"count": 1}
    ev.refresh_from_db()
    assert (ev.status, ev.attempts, ev.error) == ("pending", 0, "")
    delay.assert_called_once_with(ev.id)


def test_danh_sach_cho_khop_va_khop_tay(viewer, editor):
    PancakePosInboundOrder.objects.create(pancake_order_id="A1", match_status="unmatched", candidates=[1, 2],
                                          reason="Mã khớp 2 đơn CRM")
    PancakePosInboundOrder.objects.create(pancake_order_id="A2", match_status="matched")
    res = viewer.get("/api/pancake-pos/inbound-orders")
    assert res.status_code == 200 and res.data["total"] == 1
    item = res.data["items"][0]
    assert (item["pancake_order_id"], item["candidates"]) == ("A1", [1, 2])
    assert viewer.get("/api/pancake-pos/inbound-orders", {"match_status": "matched"}).data["total"] == 1
    assert viewer.get("/api/pancake-pos/inbound-orders", {"match_status": "xyz"}).status_code == 400
    url = f"/api/pancake-pos/inbound-orders/{item['id']}/resolve"
    assert editor.post(url, {"action": "match"}, format="json").status_code == 400  # thiếu order_id
    assert editor.post(url, {"action": "match", "order_id": 999999}, format="json").status_code == 400
    assert editor.post("/api/pancake-pos/inbound-orders/999999/resolve", {"action": "ignore"},
                       format="json").status_code == 404
    res = editor.post(url, {"action": "ignore"}, format="json")
    assert res.status_code == 200 and res.data["match_status"] == "ignored"


# ── lệnh ────────────────────────────────────────────────────────────────────
def test_lenh_dry_run_va_show():
    out = StringIO()
    with override_sysconfig(**CONFIG), patch(
        "apps.pancake_pos.application.services.webhook_config.build_client"
    ) as build:
        call_command("pancake_pos_webhook", "configure", "--dry-run", stdout=out)
        call_command("pancake_pos_webhook", "show", stdout=out)
    build.assert_not_called()
    text = out.getvalue()
    assert "X-Desi-Webhook-Secret" in text and SECRET not in text and "Dry-run" in text
    with override_sysconfig(PANCAKE_POS_WEBHOOK_SECRET=""), pytest.raises(CommandError):
        call_command("pancake_pos_webhook", "configure", "--dry-run", stdout=StringIO())
