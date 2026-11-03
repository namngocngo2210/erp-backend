"""API Pancake POS: quyền `pancake-pos:view|edit`, xếp lượt chạy (409 khi đang chạy),
nhật ký, liên kết + thử lại, trạng thái, kiểm tra kết nối (không lộ API key)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosApiError
from apps.pancake_pos.models import (
    LinkStatus,
    PancakePosDirtyProduct,
    PancakePosProductLink,
    PancakePosSyncRun,
    PancakePosVariationLink,
)
from apps.sysconfig import override_sysconfig
from apps.users.tests.helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db

BASE = "/api/pancake-pos"
VIEWS = "apps.pancake_pos.transport.views"
SECRET = "sk-live-SECRET-xyz"


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def editor():
    return _client(make_user_with_permissions("ppos_editor", ["pancake-pos:view", "pancake-pos:edit"]))


@pytest.fixture
def viewer():
    return _client(make_user_with_permissions("ppos_viewer", ["pancake-pos:view"]))


@pytest.fixture
def stranger():
    return _client(make_user_with_permissions("ppos_stranger", ["orders:view"]))


def _links():
    group = PancakePosProductLink.objects.create(group_key="OHI01", pancake_product_id="p1")
    den = Product.objects.create(sku="OHI01-DEN", name="Ghế Đen")
    trang = Product.objects.create(sku="OHI01-TRANG", name="Ghế Trắng")
    ok = PancakePosVariationLink.objects.create(
        product=den, product_link=group, pancake_variation_id="v1", status=LinkStatus.SYNCED
    )
    bad = PancakePosVariationLink.objects.create(
        product=trang, product_link=group, status=LinkStatus.CONFLICT, last_error="SKU khớp 2 mẫu mã"
    )
    return ok, bad


# ── quyền ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["sync-runs", "links", "status", "sync-runs/1"])
def test_khong_co_quyen_view_bi_403(stranger, path):
    assert stranger.get(f"{BASE}/{path}").status_code == 403


def test_view_khong_duoc_chay_dong_bo_hay_thu_lai(viewer):
    _, bad = _links()
    assert viewer.post(f"{BASE}/sync", {"mode": "dry_run"}, format="json").status_code == 403
    assert viewer.post(f"{BASE}/links/{bad.id}/retry").status_code == 403
    assert viewer.get(f"{BASE}/links").status_code == 200


def test_admin_co_du_quyen():
    admin = _client(make_admin("ppos_admin"))
    assert admin.get(f"{BASE}/status").status_code == 200


# ── chạy đồng bộ ────────────────────────────────────────────────────────────
def test_chay_thu_xep_hang_va_tra_run_id(editor):
    with patch(f"{VIEWS}.run_sync") as task:
        resp = editor.post(f"{BASE}/sync", {"mode": "dry_run", "limit": 10, "skus": [" OHI01-DEN "]}, format="json")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    run = PancakePosSyncRun.objects.get(id=run_id)
    assert run.mode == "dry_run" and run.finished_at is None and run.triggered_by is not None
    args = task.delay.call_args.args
    assert args[:4] == (run_id, "dry_run", 10, ["OHI01-DEN"])


def test_khong_chay_2_luot_song_song(editor):
    with patch(f"{VIEWS}.run_sync"):
        assert editor.post(f"{BASE}/sync", {"mode": "dry_run"}, format="json").status_code == 202
        with override_sysconfig(PANCAKE_POS_ENABLED=True):
            resp = editor.post(f"{BASE}/sync", {"mode": "full"}, format="json")
    assert resp.status_code == 409
    assert PancakePosSyncRun.objects.count() == 1


def test_luot_treo_qua_han_khoa_khong_chan(editor):
    stale = PancakePosSyncRun.objects.create(mode="full")
    PancakePosSyncRun.objects.filter(id=stale.id).update(started_at=timezone.now() - timedelta(hours=3))
    with patch(f"{VIEWS}.run_sync"):
        assert editor.post(f"{BASE}/sync", {"mode": "dry_run"}, format="json").status_code == 202


def test_full_khi_co_tat_bi_409(editor):
    with override_sysconfig(PANCAKE_POS_ENABLED=False), patch(f"{VIEWS}.run_sync") as task:
        resp = editor.post(f"{BASE}/sync", {"mode": "full"}, format="json")
    assert resp.status_code == 409
    task.delay.assert_not_called()


@pytest.mark.parametrize("body", [{"mode": "incremental"}, {"mode": "dry_run", "limit": 0}, {}])
def test_body_sai_400(editor, body):
    assert editor.post(f"{BASE}/sync", body, format="json").status_code == 400


def test_broker_chet_thi_dong_nhat_ky(editor):
    with patch(f"{VIEWS}.run_sync") as task:
        task.delay.side_effect = ConnectionError("broker")
        editor.raise_request_exception = False
        resp = editor.post(f"{BASE}/sync", {"mode": "dry_run"}, format="json")
    assert resp.status_code == 500
    run = PancakePosSyncRun.objects.get()
    assert run.finished_at is not None and run.report["fatal"]


# ── nhật ký ─────────────────────────────────────────────────────────────────
def test_danh_sach_luot_chay_phan_trang_khong_keo_report(viewer):
    for i in range(3):
        PancakePosSyncRun.objects.create(mode="dry_run", created=i, finished_at=timezone.now(),
                                         report={"samples": [{"x": i}], **({"fatal": "boom"} if i == 2 else {})})
    data = viewer.get(f"{BASE}/sync-runs?limit=2").json()
    assert data["total"] == 3 and len(data["items"]) == 2
    first = data["items"][0]
    assert first["created"] == 2 and first["fatal"] == "boom" and "report" not in first
    assert viewer.get(f"{BASE}/sync-runs?limit=2&offset=2").json()["items"][0]["created"] == 0
    assert viewer.get(f"{BASE}/sync-runs?limit=x").status_code == 400


def test_chi_tiet_luot_chay_kem_report(viewer):
    run = PancakePosSyncRun.objects.create(mode="dry_run", hidden=2, report={"samples": [{"group_key": "OHI01"}]})
    data = viewer.get(f"{BASE}/sync-runs/{run.id}").json()
    assert data["report"]["samples"][0]["group_key"] == "OHI01" and data["hidden"] == 2
    assert viewer.get(f"{BASE}/sync-runs/999999").status_code == 404


# ── liên kết ────────────────────────────────────────────────────────────────
def test_loc_lien_ket_theo_trang_thai_va_tu_khoa(viewer):
    ok, bad = _links()
    data = viewer.get(f"{BASE}/links?status=conflict").json()
    assert [i["id"] for i in data["items"]] == [bad.id]
    assert data["items"][0]["sku"] == "OHI01-TRANG" and data["items"][0]["group_key"] == "OHI01"
    assert [i["id"] for i in viewer.get(f"{BASE}/links?q=đen").json()["items"]] == [ok.id]
    assert viewer.get(f"{BASE}/links?status=xx").status_code == 400


def test_thu_lai_lien_ket_dua_vao_hang_doi(editor):
    _, bad = _links()
    with override_sysconfig(PANCAKE_POS_ENABLED=True):
        resp = editor.post(f"{BASE}/links/{bad.id}/retry")
    assert resp.status_code == 202 and resp.json()["status"] == "pending"
    bad.refresh_from_db()
    assert bad.status == LinkStatus.PENDING
    assert PancakePosDirtyProduct.objects.filter(product_id=bad.product_id).exists()
    assert editor.post(f"{BASE}/links/999999/retry").status_code == 404


def test_thu_lai_khi_co_tat_409(editor):
    _, bad = _links()
    with override_sysconfig(PANCAKE_POS_ENABLED=False):
        assert editor.post(f"{BASE}/links/{bad.id}/retry").status_code == 409


# ── trạng thái + kiểm tra kết nối ───────────────────────────────────────────
def test_trang_thai_cuc_bo(viewer):
    _links()
    PancakePosDirtyProduct.objects.create(product_id=1)
    with override_sysconfig(PANCAKE_POS_ENABLED=True, PANCAKE_POS_API_KEY=SECRET, PANCAKE_POS_SHOP_ID=42):
        data = viewer.get(f"{BASE}/status").json()
    assert data["enabled"] is True and data["api_key_set"] is True and data["shop_id"] == 42
    assert data["dirty_count"] == 1
    assert data["link_counts"]["synced"] == 1 and data["link_counts"]["conflict"] == 1
    assert SECRET not in str(data)


def test_health_liet_ke_shop_khong_lo_key(viewer):
    client = MagicMock()
    client.list_shops.return_value = [{"id": 42, "name": "Desi"}, {"id": 7, "name": "Khác"}]
    with override_sysconfig(PANCAKE_POS_API_KEY=SECRET, PANCAKE_POS_SHOP_ID=42), patch(
        f"{VIEWS}.build_client", return_value=client
    ):
        data = viewer.get(f"{BASE}/health").json()
    assert data["ok"] is True and data["shop_found"] is True
    assert data["shops"] == [{"id": 42, "name": "Desi"}, {"id": 7, "name": "Khác"}]
    assert SECRET not in str(data)
    client.close.assert_called_once()


def test_health_key_sai_tra_loi_ro_rang(viewer):
    client = MagicMock()
    client.list_shops.side_effect = PancakePosApiError("GET /shops: HTTP 401 — invalid api_key=***")
    with override_sysconfig(PANCAKE_POS_API_KEY=SECRET), patch(f"{VIEWS}.build_client", return_value=client):
        data = viewer.get(f"{BASE}/health").json()
    assert data["ok"] is False and "401" in data["error"] and data["shops"] == []


def test_health_chua_nhap_key(viewer):
    with override_sysconfig(PANCAKE_POS_API_KEY=""):
        data = viewer.get(f"{BASE}/health").json()
    assert data["ok"] is False and "API_KEY" in data["error"]
