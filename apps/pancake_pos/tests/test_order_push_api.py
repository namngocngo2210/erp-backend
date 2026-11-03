"""API đẩy đơn (two-way-sync A2): danh sách liên kết, thử lại, chọn cách xuất, mã đơn
Pancake ở trang đơn — quyền, cờ tắt; event orders → task; client đơn hàng (HTTP giả)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.contracts.order_events import OrderSaved, OrderStatusChanged
from apps.orders.models import Order, OrderItem
from apps.pancake_pos import subscribers
from apps.pancake_pos.application.services import order_push_jobs
from apps.pancake_pos.infrastructure.pancake_pos_client import (
    PancakePosApiError,
    PancakePosClient,
    PancakePosTransportError,
    parse_order,
)
from apps.pancake_pos.models import OrderLinkStatus, PancakePosOrderLink, PancakePosWarehouseLink
from apps.sysconfig import override_sysconfig
from apps.users.tests.helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse

VIEWS = "apps.pancake_pos.transport.views"
ON = {"PANCAKE_POS_ENABLED": True, "PANCAKE_POS_PUSH_ORDERS_ENABLED": True}


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def editor(db):
    return _client(make_user_with_permissions("ord_editor", ["pancake-pos:view", "pancake-pos:edit"]))


@pytest.fixture
def viewer(db):
    return _client(make_user_with_permissions("ord_viewer", ["pancake-pos:view"]))


@pytest.fixture
def order_viewer(db):
    return _client(make_user_with_permissions("ord_sale", ["orders:view"]))


@pytest.fixture
def outsider(db):
    return _client(make_user_with_permissions("ord_none", ["warehouses:view"]))


@pytest.fixture
def setup(db):
    wh = Warehouse.objects.create(code="DM1", name="Đại Mỗ 1")
    PancakePosWarehouseLink.objects.create(warehouse=wh, pancake_warehouse_id="pw-1")
    channel = Channel.objects.create(code="ch-api", name="Kênh")
    customer = Customer.objects.create(full_name="K", phone_raw="0911", phone_normalized="0911", code="C-API")
    product = Product.objects.create(name="SP", sku="API-1")
    order = Order.objects.create(channel=channel, customer=customer, name="Khách API", phone="0911",
                                 order_source="landing", total_amount=1, total_after_discount=1)
    OrderItem.objects.create(order=order, product=product, sku="API-1", quantity=2, unit_price=1, line_total=2)
    return {"warehouse": wh, "order": order, "product": product}


def _link(order, **kw) -> PancakePosOrderLink:
    return PancakePosOrderLink.objects.create(order=order, **kw)


# ── danh sách ───────────────────────────────────────────────────────────────
def test_danh_sach_loc_trang_thai_va_tim(viewer, setup):
    order = setup["order"]
    _link(order, status=OrderLinkStatus.NEEDS_ROUTING, last_error="Không kho nào đủ")
    res = viewer.get("/api/pancake-pos/order-links", {"status": "error,needs_routing"})
    assert res.status_code == 200 and res.data["total"] == 1
    item = res.data["items"][0]
    assert (item["order_id"], item["status"], item["order_name"], item["order_source"]) == (
        order.id, "needs_routing", "Khách API", "landing",
    )
    assert viewer.get("/api/pancake-pos/order-links", {"q": str(order.id)}).data["total"] == 1
    assert viewer.get("/api/pancake-pos/order-links", {"q": "không-có"}).data["total"] == 0
    assert viewer.get("/api/pancake-pos/order-links", {"status": "xyz"}).status_code == 400


def test_danh_sach_can_quyen(outsider, setup):
    assert outsider.get("/api/pancake-pos/order-links").status_code == 403


def test_status_co_co_va_dem(viewer, setup):
    _link(setup["order"], status=OrderLinkStatus.ERROR)
    with override_sysconfig(PANCAKE_POS_PUSH_ORDERS_ENABLED=True):
        data = viewer.get("/api/pancake-pos/status").data
    assert data["push_orders_enabled"] is True
    assert data["order_link_counts"]["error"] == 1 and data["order_link_counts"]["pushed"] == 0


# ── mã đơn Pancake ở trang đơn ──────────────────────────────────────────────
def test_ma_don_pancake_cho_nguoi_xem_don(order_viewer, outsider, setup):
    order = setup["order"]
    _link(order, part_no=1, pancake_order_id="P-1", status="pushed")
    _link(order, part_no=2, pancake_order_id="P-2", status="pushed")
    res = order_viewer.get(f"/api/pancake-pos/orders/{order.id}/refs")
    assert res.status_code == 200
    assert [i["pancake_order_id"] for i in res.data["items"]] == ["P-1", "P-2"]
    assert outsider.get(f"/api/pancake-pos/orders/{order.id}/refs").status_code == 403


# ── chọn cách xuất ──────────────────────────────────────────────────────────
def test_ngu_canh_va_chon_kho(editor, setup):
    order, wh = setup["order"], setup["warehouse"]
    _link(order, status=OrderLinkStatus.NEEDS_ROUTING)
    ctx = editor.get(f"/api/pancake-pos/orders/{order.id}/routing")
    assert ctx.status_code == 200 and ctx.data["lines"][0]["quantity"] == 2
    with override_sysconfig(**ON), patch(f"{VIEWS}.push_orders_for") as task:
        res = editor.post(f"/api/pancake-pos/orders/{order.id}/routing",
                          {"parts": [{"warehouse_id": wh.id}]}, format="json")
    assert res.status_code == 202, res.data
    task.delay.assert_called_once_with([order.id], True, False)
    link = PancakePosOrderLink.objects.get(order=order)
    assert (link.status, link.pancake_warehouse_id, link.items) == ("pending", "pw-1", None)


def test_chon_cach_xuat_sai_400_va_da_day_409(editor, setup):
    order, wh, product = setup["order"], setup["warehouse"], setup["product"]
    bad = {"parts": [{"warehouse_id": wh.id, "items": [{"product_id": product.id, "quantity": 5}]},
                     {"warehouse_id": wh.id, "items": [{"product_id": product.id, "quantity": 1}]}]}
    with override_sysconfig(**ON), patch(f"{VIEWS}.push_orders_for"):
        assert editor.post(f"/api/pancake-pos/orders/{order.id}/routing", bad, format="json").status_code == 400
        _link(order, pancake_order_id="P-9", status="pushed")
        res = editor.post(f"/api/pancake-pos/orders/{order.id}/routing",
                          {"parts": [{"warehouse_id": wh.id}]}, format="json")
    assert res.status_code == 409


def test_chon_cach_xuat_can_quyen_sua_va_co_bat(viewer, editor, setup):
    order, wh = setup["order"], setup["warehouse"]
    body = {"parts": [{"warehouse_id": wh.id}]}
    with override_sysconfig(**ON):
        assert viewer.post(f"/api/pancake-pos/orders/{order.id}/routing", body, format="json").status_code == 403
    with override_sysconfig(PANCAKE_POS_ENABLED=True, PANCAKE_POS_PUSH_ORDERS_ENABLED=False):
        assert editor.post(f"/api/pancake-pos/orders/{order.id}/routing", body, format="json").status_code == 409
    assert editor.get("/api/pancake-pos/orders/999999/routing").status_code == 404


# ── thử lại ─────────────────────────────────────────────────────────────────
def test_thu_lai(editor, viewer, setup):
    order = setup["order"]
    link = _link(order, status=OrderLinkStatus.ERROR, pancake_warehouse_id="pw-1", attempts=3)
    with override_sysconfig(**ON), patch(f"{VIEWS}.push_orders_for") as task:
        assert viewer.post(f"/api/pancake-pos/order-links/{link.id}/retry").status_code == 403
        res = editor.post(f"/api/pancake-pos/order-links/{link.id}/retry")
        assert res.status_code == 202
        task.delay.assert_called_once_with([order.id], True, False)
        link.refresh_from_db()
        assert (link.status, link.attempts) == ("pending", 0)
        PancakePosOrderLink.objects.filter(id=link.id).update(status="pushing")
        assert editor.post(f"/api/pancake-pos/order-links/{link.id}/retry").status_code == 409
        assert editor.post("/api/pancake-pos/order-links/999999/retry").status_code == 404


def test_van_day_chi_cho_don_bi_bo_qua_vi_trung(editor, setup):
    order = setup["order"]
    link = _link(order, status=OrderLinkStatus.ERROR, pancake_warehouse_id="pw-1")
    with override_sysconfig(**ON), patch(f"{VIEWS}.push_orders_for") as task:
        url = f"/api/pancake-pos/order-links/{link.id}/retry"
        assert editor.post(url, {"force": True}, format="json").status_code == 409
        PancakePosOrderLink.objects.filter(id=link.id).update(status=OrderLinkStatus.SKIPPED_DUPLICATE)
        res = editor.post(url, {"force": True}, format="json")
        assert res.status_code == 202 and res.json() == {"order_id": order.id}
        task.delay.assert_called_once_with([order.id], True, True)
    assert not PancakePosOrderLink.objects.filter(order_id=order.id).exists()  # đánh giá lại


def test_thu_lai_don_da_day_la_doi_chieu_lai(editor, setup):
    link = _link(setup["order"], status="pushed", pancake_order_id="P-1", payload_hash="h")
    with override_sysconfig(**ON), patch(f"{VIEWS}.push_orders_for"):
        assert editor.post(f"/api/pancake-pos/order-links/{link.id}/retry").status_code == 202
    link.refresh_from_db()
    assert (link.status, link.payload_hash, link.checked_at) == ("pushed", "", None)


# ── event + cờ ──────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_event_chi_xep_task_khi_bat_co():
    with patch("apps.pancake_pos.tasks.push_orders_for") as task:
        with override_sysconfig(PANCAKE_POS_ENABLED=True, PANCAKE_POS_PUSH_ORDERS_ENABLED=False):
            subscribers._on_order_status_changed(OrderStatusChanged(order_id=5, old_status=0, new_status=6))
        task.delay.assert_not_called()
        with override_sysconfig(**ON):
            subscribers._on_order_status_changed(OrderStatusChanged(order_id=5, old_status=0, new_status=6))
            subscribers._on_order_saved(OrderSaved(order_id=6, update_fields=("sales_closed_at",)))
            subscribers._on_order_saved(OrderSaved(order_id=7, update_fields=("name",)))
            subscribers._on_order_saved(OrderSaved(order_id=8, update_fields=None))
        assert [c.args[0] for c in task.delay.call_args_list] == [[5], [6]]


@pytest.mark.django_db
def test_job_tat_co_la_no_op():
    with override_sysconfig(PANCAKE_POS_ENABLED=True, PANCAKE_POS_PUSH_ORDERS_ENABLED=False), \
            patch.object(order_push_jobs, "build_order_push_service") as build:
        assert order_push_jobs.run_cycle() == {"reason": "disabled"}
        assert order_push_jobs.run_for_orders([1]) == {"reason": "disabled"}
    build.assert_not_called()


@pytest.mark.django_db
def test_job_bat_co_chay_du_ba_pha():
    service = MagicMock()
    service.discover.return_value = {"queued": 1}
    service.push.return_value = {"pushed": 1}
    service.sync_changes.return_value = {}
    with override_sysconfig(**ON), patch.object(order_push_jobs, "build_order_push_service", return_value=service):
        out = order_push_jobs.run_for_orders([3, 3, 2], manual=True)
    assert out == {"discover": {"queued": 1}, "push": {"pushed": 1}, "changes": {}}
    service.discover.assert_called_once_with([2, 3], ignore_lookback=True, force_ids=())


# ── client đơn hàng (HTTP giả) ──────────────────────────────────────────────
def _resp(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def _pos(responses):
    http = MagicMock()
    http.request.side_effect = responses
    return PancakePosClient("KEY", 42, rps=1000, max_retries=3, http=http, sleep=lambda s: None), http


def test_client_tao_don_khong_retry_5xx():
    client, http = _pos([_resp({"success": False, "message": "boom"}, 502)])
    with pytest.raises(PancakePosApiError) as exc:
        client.create_order({"custom_id": "CRM-1"})
    assert exc.value.status_code == 502 and http.request.call_count == 1


def test_client_tao_don_khong_retry_read_timeout():
    import requests

    client, http = _pos([requests.ReadTimeout("t")])
    with pytest.raises(PancakePosTransportError):
        client.create_order({})
    assert http.request.call_count == 1


def test_client_tao_don_parse_id():
    client, http = _pos([_resp({"success": True, "data": {"id": 123, "status": 0, "custom_id": "CRM-1"}})])
    order = client.create_order({"custom_id": "CRM-1"})
    assert (order.id, order.status, order.custom_id) == ("123", 0, "CRM-1")
    assert http.request.call_args.args[:2] == ("POST", "/shops/42/orders")


def test_client_tim_don_loc_custom_id():
    client, http = _pos([_resp({"success": True, "data": [
        {"id": 1, "custom_id": "CRM-10", "status": 0},
        {"id": 2, "custom_id": "CRM-100", "status": 0},
        {"id": 3, "custom_id": "", "status": 0},
    ]})])
    found = client.find_orders(custom_id="CRM-10", search="0911", since=1700000000)
    assert [o.id for o in found] == ["1"]
    params = http.request.call_args.kwargs["params"]
    assert (params["search"], params["startDateTime"]) == ("0911", 1700000000)


def test_client_get_order_404_la_none_va_put():
    client, http = _pos([_resp({"success": False, "message": "not found"}, 404),
                         _resp({"id": 5, "status": 6})])
    assert client.get_order("5") is None
    assert client.update_order("5", {"status": 6}).status == 6
    assert http.request.call_args.args[:2] == ("PUT", "/shops/42/orders/5")


def test_parse_order_phong_thu():
    assert parse_order(None) is None
    assert parse_order({"id": None}) is None
    assert parse_order({"id": "x", "status": "abc"}).status is None
