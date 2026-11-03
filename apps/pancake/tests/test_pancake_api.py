"""API Pancake: cấu hình chỉ superuser; danh sách lead theo quyền `pancake-leads:view`."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.pancake.infrastructure.pancake_client import PageDTO, PancakeError, PancakeTokenError
from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage, PancakePushStatus, PancakeStaffMap
from apps.users.tests.helpers import make_admin, make_user_with_permissions, make_user_with_sections

pytestmark = pytest.mark.django_db

BASE = "/api/pancake"
VIEWS = "apps.pancake.transport.views"


@pytest.fixture
def su():
    client = APIClient()
    client.force_authenticate(user=make_admin("pancake_admin"))
    return client


@pytest.fixture
def viewer():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("sale", ["pancake-leads"]))
    return client


@pytest.fixture
def stranger():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("kho", ["orders"]))
    return client


def _page(**kw):
    values = {"pancake_page_id": "945997011940587", "name": "DELAX", "platform": "facebook"}
    values.update(kw)
    return PancakePage.objects.create(**values)


# ── connection ────────────────────────────────────────────────────────────────

def test_connection_get_khong_lo_token(su):
    PancakeConnection.objects.create(access_token="secret")
    data = su.get(f"{BASE}/connection").json()
    assert data["has_token"] is True
    assert "secret" not in str(data) and "access_token" not in data


def test_connection_get_chua_co(su):
    assert su.get(f"{BASE}/connection").json()["has_token"] is False


def test_connection_put_kiem_tra_ngay(su):
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.return_value = [
            PageDTO(id="1", name="P", platform="facebook", is_activated=True, has_active_subscription=True, role_in_page="ADMINISTER")
        ]
        resp = su.put(f"{BASE}/connection", {"access_token": "tok"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["auth_status"] == "ok"
    conn = PancakeConnection.get_active()
    assert conn.access_token == "tok"
    assert PancakePage.objects.filter(pancake_page_id="1").exists()


def test_connection_put_token_hong_400(su):
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.side_effect = PancakeTokenError("Invalid token")
        resp = su.put(f"{BASE}/connection", {"access_token": "bad"}, format="json")
    assert resp.status_code == 400
    assert "Invalid token" in resp.json()["detail"]
    assert PancakeConnection.get_active().auth_status == "error"


def test_connection_put_thieu_token_400(su):
    assert su.put(f"{BASE}/connection", {"access_token": ""}, format="json").status_code == 400


def test_connection_put_loi_mang_400(su):
    """Lỗi mạng (không phải token sai) từ client vẫn phải 400 + ghi lại auth_status
    lỗi — không được để nguyên trạng thái OK cũ của lần kiểm tra trước."""
    PancakeConnection.objects.create(access_token="cu", auth_status="ok")
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.side_effect = PancakeError("Không gọi được Pancake (ConnectionError)")
        resp = su.put(f"{BASE}/connection", {"access_token": "tok"}, format="json")
    assert resp.status_code == 400
    assert "Không gọi được Pancake" in resp.json()["detail"]
    conn = PancakeConnection.get_active()
    assert conn.auth_status == "error"
    assert "Không gọi được Pancake" in conn.auth_error


def test_cau_hinh_chi_superuser(viewer):
    assert viewer.get(f"{BASE}/connection").status_code == 403
    assert viewer.post(f"{BASE}/pages/sync").status_code == 403
    # Gate chạy TRƯỚC khi chạm DB — 403 dù page id không tồn tại.
    assert viewer.patch(f"{BASE}/pages/1", {"is_enabled": False}, format="json").status_code == 403


def test_pages_list_cho_user_pancake_leads(viewer):
    channel = Channel.objects.create(code="a", name="Kênh A")
    _page(channel=channel, page_access_token="t", is_enabled=True)
    resp = viewer.get(f"{BASE}/pages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pages"][0]["pancake_page_id"] == "945997011940587"
    raw = resp.content.decode()
    assert "page_access_token" not in raw and '"access_token"' not in raw


def test_pages_list_stranger_403(stranger):
    assert stranger.get(f"{BASE}/pages").status_code == 403


# ── pages ─────────────────────────────────────────────────────────────────────

def test_pages_sync(su):
    PancakeConnection.objects.create(access_token="tok")
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.return_value = [
            PageDTO(id="zl_1", name="Zalo", platform="zalo", is_activated=True, has_active_subscription=True, role_in_page="ADMINISTER")
        ]
        resp = su.post(f"{BASE}/pages/sync")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    assert resp.json()["pages"][0]["foxia_kind"] == "zalo"


def test_pages_sync_chua_co_token_400(su):
    assert su.post(f"{BASE}/pages/sync").status_code == 400


def test_pages_list_shape(su):
    channel = Channel.objects.create(code="a", name="Kênh A")
    _page(channel=channel, page_access_token="t", token_error="", is_enabled=True)
    _page(pancake_page_id="tts_1", name="Tiktok", platform="tiktok_shop")
    data = su.get(f"{BASE}/pages").json()
    by_id = {p["pancake_page_id"]: p for p in data["pages"]}
    p = by_id["945997011940587"]
    assert (p["channel_id"], p["channel_name"], p["foxia_kind"], p["is_enabled"], p["has_token"]) == (channel.id, "Kênh A", "messenger", True, True)
    assert "page_access_token" not in p
    assert by_id["tts_1"]["foxia_kind"] is None


def test_pages_patch_gan_kenh(su):
    channel = Channel.objects.create(code="a", name="A")
    page = _page()
    resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": channel.id}, format="json")
    assert resp.status_code == 200 and resp.json()["channel_id"] == channel.id
    resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": None}, format="json")
    assert resp.status_code == 200 and resp.json()["channel_id"] is None


def test_pages_patch_kenh_khong_ton_tai_400(su):
    page = _page()
    assert su.patch(f"{BASE}/pages/{page.id}", {"channel_id": 999999}, format="json").status_code == 400


def test_pages_patch_channel_id_khong_phai_so_400(su):
    page = _page()
    assert su.patch(f"{BASE}/pages/{page.id}", {"channel_id": "abc"}, format="json").status_code == 400


def test_pages_patch_bat_thieu_kenh_400(su):
    page = _page()
    resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": True}, format="json")
    assert resp.status_code == 400 and "kênh" in resp.json()["detail"]


def test_pages_patch_bat_sinh_token(su):
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    page = _page()
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.generate_page_token.return_value = "ptok"
        resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": channel.id, "is_enabled": True}, format="json")
    assert resp.status_code == 200
    assert (resp.json()["is_enabled"], resp.json()["has_token"]) == (True, True)
    page.refresh_from_db()
    assert page.page_access_token == "ptok" and page.polled_until is not None


def test_pages_patch_bat_sinh_token_loi_400_khong_bat(su):
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    page = _page(channel=channel)
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.generate_page_token.side_effect = PancakeTokenError("Thiếu quyền Admin")
        resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": True}, format="json")
    assert resp.status_code == 400 and "Thiếu quyền Admin" in resp.json()["detail"]
    page.refresh_from_db()
    assert page.is_enabled is False and page.token_error == "Thiếu quyền Admin"


def test_pages_patch_tat(su):
    channel = Channel.objects.create(code="a", name="A")
    page = _page(channel=channel, is_enabled=True, page_access_token="t")
    resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": False}, format="json")
    assert resp.status_code == 200 and resp.json()["is_enabled"] is False


def test_pages_patch_go_kenh_page_dang_bat_thi_tu_tat(su):
    channel = Channel.objects.create(code="a", name="A")
    page = _page(channel=channel, is_enabled=True, page_access_token="t")
    resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": None}, format="json")
    assert resp.status_code == 200
    assert (resp.json()["channel_id"], resp.json()["is_enabled"]) == (None, False)
    page.refresh_from_db()
    assert page.channel_id is None and page.is_enabled is False


# ── leads ─────────────────────────────────────────────────────────────────────

def _lead(page, channel, phone, **kw):
    values = {"page": page, "channel": channel, "conversation_id": f"c-{phone}", "phone_raw": phone,
              "phone": phone, "foxia_kind": "messenger", "customer_name": "Khách"}
    values.update(kw)
    return PancakeLead.objects.create(**values)


def _order(channel, **kw):
    # Order.customer PROTECT không null — dựng kèm Customer như test_pancake_models.
    values = {
        "channel": channel, "order_source": "messenger",
        "customer": Customer.objects.create(full_name="Khach test"),
        "name": "Nguyễn Văn A", "phone": "0909999999", "address": "9b Lê Quý Đôn",
        "ward": "Phường Bạch Đằng", "district": "Hai Bà Trưng", "province": "Hà Nội",
    }
    values.update(kw)
    return Order.objects.create(**values)


def test_leads_list_theo_quyen(viewer, stranger):
    assert viewer.get(f"{BASE}/leads").status_code == 200
    assert stranger.get(f"{BASE}/leads").status_code == 403


def test_leads_list_loc_va_phan_trang(viewer):
    a = Channel.objects.create(code="a", name="A")
    b = Channel.objects.create(code="b", name="B")
    page = _page(channel=a)
    order = _order(a)
    _lead(page, a, "0901234567", push_status=PancakePushStatus.ORDER_CREATED, cohoi_id=5, tags=["BOT"], order=order)
    _lead(page, b, "0912345678", push_status=PancakePushStatus.FAILED, customer_name="Tuấn")

    data = viewer.get(f"{BASE}/leads").json()
    assert data["total"] == 2 and [i["phone"] for i in data["items"]] == ["0912345678", "0901234567"]
    assert data["items"][1]["tags"] == ["BOT"] and data["items"][1]["cohoi_id"] == 5
    assert data["items"][1]["page_name"] == "DELAX" and data["items"][1]["channel_name"] == "A"
    assert data["items"][1]["order_id"] == order.id
    assert data["items"][0]["order_id"] is None

    # Lọc theo trạng thái mới (order_created/order_failed) — thêm sau 17/09.
    assert viewer.get(f"{BASE}/leads", {"push_status": "order_created"}).json()["total"] == 1

    assert viewer.get(f"{BASE}/leads", {"push_status": "failed"}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"channel": b.id}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"page": page.id}).json()["total"] == 2
    assert viewer.get(f"{BASE}/leads", {"q": "0912"}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"q": "tuấn"}).json()["total"] == 1
    # Gõ dạng quốc tế: chỉ khớp được nhờ phone_search_candidates chuẩn hoá về "0…".
    q_intl = viewer.get(f"{BASE}/leads", {"q": "+84 912 345 678"}).json()
    assert q_intl["total"] == 1 and q_intl["items"][0]["phone"] == "0912345678"
    assert viewer.get(f"{BASE}/leads", {"date_from": "2099-01-01"}).json()["total"] == 0
    paged = viewer.get(f"{BASE}/leads", {"limit": 1, "offset": 1}).json()
    assert (len(paged["items"]), paged["limit"], paged["offset"]) == (1, 1, 1)


def test_leads_retry_order(viewer, stranger):
    a = Channel.objects.create(code="a", name="A")
    page = _page(channel=a)
    order = _order(a)
    # Đã có order (order_exists) — order_id không rỗng nên KHÔNG đủ điều kiện,
    # dù push_status nghe như "đã xong" chứ không phải lỗi.
    has_order = _lead(page, a, "0901234567", push_status=PancakePushStatus.ORDER_EXISTS, order=order)
    failed = _lead(page, a, "0912345678", push_status=PancakePushStatus.ORDER_FAILED)

    assert stranger.post(f"{BASE}/leads/{failed.id}/retry-order").status_code == 403
    assert viewer.post(f"{BASE}/leads/{has_order.id}/retry-order").status_code == 409
    with patch(f"{VIEWS}.retry_lead_order") as task:
        resp = viewer.post(f"{BASE}/leads/{failed.id}/retry-order")
    assert resp.status_code == 202 and resp.json() == {"status": "queued"}
    task.delay.assert_called_once_with(failed.id)
    assert viewer.post(f"{BASE}/leads/999999/retry-order").status_code == 404


def test_leads_retry_order_requires_edit_permission():
    """Tạo lại đơn là hành vi GHI: chỉ `pancake-leads:view` phải nhận 403 (audit
    2026-09-23 M1). Role cũ được migration users/0052 grandfather :edit."""
    a = Channel.objects.create(code="a", name="A")
    page = _page(channel=a)
    failed = _lead(page, a, "0912345678", push_status=PancakePushStatus.ORDER_FAILED)
    view_only = APIClient()
    view_only.force_authenticate(user=make_user_with_permissions("pancake_view_only", ["pancake-leads:view"]))

    with patch(f"{VIEWS}.retry_lead_order") as task:
        resp = view_only.post(f"{BASE}/leads/{failed.id}/retry-order")
    assert resp.status_code == 403
    task.delay.assert_not_called()
    # Trang danh sách vẫn mở với :view.
    assert view_only.get(f"{BASE}/leads").status_code == 200


def test_leads_retry_order_pending(viewer):
    """Worker bị giết giữa lúc tạo lead và create_order_for_lead để lại lead
    `pending` mãi mãi nếu không cho tạo lại đơn — spec §9."""
    a = Channel.objects.create(code="a", name="A")
    page = _page(channel=a)
    pending = _lead(page, a, "0900000000", push_status=PancakePushStatus.PENDING)
    with patch(f"{VIEWS}.retry_lead_order") as task:
        resp = viewer.post(f"{BASE}/leads/{pending.id}/retry-order")
    assert resp.status_code == 202
    task.delay.assert_called_once_with(pending.id)


def test_leads_repush_da_xoa(viewer):
    """Endpoint `repush` cũ (đẩy Foxia trực tiếp) không còn — thay bằng retry-order."""
    a = Channel.objects.create(code="a", name="A")
    page = _page(channel=a)
    failed = _lead(page, a, "0912345678", push_status=PancakePushStatus.FAILED)
    assert viewer.post(f"{BASE}/leads/{failed.id}/repush").status_code == 404


# ── staff-maps ────────────────────────────────────────────────────────────────

def test_staff_maps_get_chi_superuser(viewer):
    assert viewer.get(f"{BASE}/staff-maps").status_code == 403


def test_staff_maps_get(su):
    staff = make_user_with_sections("sale1", ["orders"])
    staff.display_name = "Nguyễn Sale"
    staff.save(update_fields=["display_name"])
    mapped = PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=staff)
    unmapped = PancakeStaffMap.objects.create(pancake_name="Chưa gán")

    data = su.get(f"{BASE}/staff-maps").json()
    by_name = {i["pancake_name"]: i for i in data["items"]}
    assert by_name["Nhung HN"] == {
        "id": mapped.id, "pancake_name": "Nhung HN", "user_id": staff.id,
        "user_username": "sale1", "user_display_name": "Nguyễn Sale", "last_seen_at": None,
    }
    assert by_name["Chưa gán"] == {
        "id": unmapped.id, "pancake_name": "Chưa gán", "user_id": None,
        "user_username": "", "user_display_name": "", "last_seen_at": None,
    }


def test_staff_maps_put_gan_user(su):
    staff = make_user_with_sections("sale2", ["orders"])
    m = PancakeStaffMap.objects.create(pancake_name="Nhung HN")
    resp = su.put(f"{BASE}/staff-maps", {"items": [{"id": m.id, "user_id": staff.id}]}, format="json")
    assert resp.status_code == 200 and resp.json()["items"][0]["user_id"] == staff.id
    m.refresh_from_db()
    assert m.user_id == staff.id


def test_staff_maps_put_bo_gan(su):
    staff = make_user_with_sections("sale3", ["orders"])
    m = PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=staff)
    resp = su.put(f"{BASE}/staff-maps", {"items": [{"id": m.id, "user_id": None}]}, format="json")
    assert resp.status_code == 200 and resp.json()["items"][0]["user_id"] is None
    m.refresh_from_db()
    assert m.user_id is None


def test_staff_maps_put_user_he_thong_400(su):
    """`resolve_ingest_staff_id` loại role hệ thống (admin) — gán sai thì đơn của
    lead sẽ rơi vào tài khoản Administrator, ngoài mọi lưới đỡ."""
    admin = make_admin("admin2")
    m = PancakeStaffMap.objects.create(pancake_name="Nhung HN")
    resp = su.put(f"{BASE}/staff-maps", {"items": [{"id": m.id, "user_id": admin.id}]}, format="json")
    assert resp.status_code == 400
    m.refresh_from_db()
    assert m.user_id is None


def test_staff_maps_put_user_khong_ton_tai_400(su):
    m = PancakeStaffMap.objects.create(pancake_name="Nhung HN")
    resp = su.put(f"{BASE}/staff-maps", {"items": [{"id": m.id, "user_id": 999999}]}, format="json")
    assert resp.status_code == 400


def test_staff_maps_map_khong_ton_tai_400(su):
    resp = su.put(f"{BASE}/staff-maps", {"items": [{"id": 999999, "user_id": None}]}, format="json")
    assert resp.status_code == 400


def test_staff_maps_put_chi_superuser(viewer):
    assert viewer.put(f"{BASE}/staff-maps", {"items": []}, format="json").status_code == 403
