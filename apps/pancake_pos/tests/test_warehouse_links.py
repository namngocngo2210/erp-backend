"""Ghép kho CRM ↔ Pancake (two-way-sync §3): UNIQUE hai phía, tối đa một kho mặc định,
API GET/PUT (quyền view/edit), kho Pancake qua client (không lộ key, không gọi thật)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.pancake_pos.domain.entities import PancakeWarehouse
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosApiError
from apps.pancake_pos.models import PancakePosWarehouseLink
from apps.pancake_pos.repositories import WarehouseLinkError, WarehouseLinkRepository, WarehouseLinkState
from apps.users.tests.helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

URL = "/api/pancake-pos/warehouse-links"
VIEWS = "apps.pancake_pos.transport.views"


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def warehouses():
    return [
        Warehouse.objects.create(code="DM1", name="Đại Mỗ 1"),
        Warehouse.objects.create(code="DM2", name="Đại Mỗ 2"),
        Warehouse.objects.create(code="VP", name="Văn phòng"),
    ]


@pytest.fixture
def editor():
    return _client(make_user_with_permissions("wh_editor", ["pancake-pos:view", "pancake-pos:edit"]))


@pytest.fixture
def viewer():
    return _client(make_user_with_permissions("wh_viewer", ["pancake-pos:view"]))


def _state(wh: Warehouse, pancake_id: str, **kw) -> WarehouseLinkState:
    return WarehouseLinkState(warehouse_id=wh.id, pancake_warehouse_id=pancake_id, **kw)


# ── repository + ràng buộc DB ───────────────────────────────────────────────
def test_unique_hai_phia_o_db(warehouses):
    a, b, _ = warehouses
    PancakePosWarehouseLink.objects.create(warehouse=a, pancake_warehouse_id="p-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        PancakePosWarehouseLink.objects.create(warehouse=b, pancake_warehouse_id="p-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        PancakePosWarehouseLink.objects.create(warehouse=a, pancake_warehouse_id="p-2")


def test_toi_da_mot_kho_mac_dinh_o_db(warehouses):
    a, b, _ = warehouses
    PancakePosWarehouseLink.objects.create(warehouse=a, pancake_warehouse_id="p-1", is_default_for_push=True)
    PancakePosWarehouseLink.objects.create(warehouse=b, pancake_warehouse_id="p-2")  # không mặc định: được
    with pytest.raises(IntegrityError), transaction.atomic():
        PancakePosWarehouseLink.objects.filter(warehouse=b).update(is_default_for_push=True)


@pytest.mark.parametrize(
    "pairs,message",
    [
        ([(0, "p-1"), (0, "p-2")], "Một kho CRM"),
        ([(0, "p-1"), (1, "p-1")], "Một kho Pancake"),
    ],
)
def test_validate_trung(warehouses, pairs, message):
    links = [_state(warehouses[i], pid) for i, pid in pairs]
    with pytest.raises(WarehouseLinkError, match=message):
        WarehouseLinkRepository.replace_all(links)


def test_validate_nhieu_mac_dinh(warehouses):
    a, b, _ = warehouses
    with pytest.raises(WarehouseLinkError, match="mặc định"):
        WarehouseLinkRepository.replace_all([
            _state(a, "p-1", is_default_for_push=True), _state(b, "p-2", is_default_for_push=True),
        ])


def test_thay_toan_bo_doi_cheo_khong_vuong_unique(warehouses):
    a, b, _ = warehouses
    WarehouseLinkRepository.replace_all([_state(a, "p-1", priority=1), _state(b, "p-2", priority=2)])
    out = WarehouseLinkRepository.replace_all([_state(a, "p-2", priority=2), _state(b, "p-1", priority=1)])
    assert [(s.warehouse_id, s.pancake_warehouse_id) for s in out] == [(b.id, "p-1"), (a.id, "p-2")]


# ── API ─────────────────────────────────────────────────────────────────────
def test_get_liet_ke_moi_kho_crm_kem_ghep(viewer, warehouses):
    a, _, _ = warehouses
    Warehouse.objects.create(code="OLD", name="Kho cũ", deleted_at="2026-01-01T00:00:00Z")
    PancakePosWarehouseLink.objects.create(
        warehouse=a, pancake_warehouse_id="p-1", pancake_warehouse_name="Kho ĐM1", is_default_for_push=True, priority=1
    )
    res = viewer.get(URL)
    assert res.status_code == 200
    rows = {r["code"]: r for r in res.json()["items"]}
    assert set(rows) == {"DM1", "DM2", "VP"}  # kho đã xoá mềm không hiện
    assert rows["DM1"] | {"warehouse_id": 0} == {
        "warehouse_id": 0, "code": "DM1", "name": "Đại Mỗ 1", "is_active": True, "pancake_warehouse_id": "p-1",
        "pancake_warehouse_name": "Kho ĐM1", "is_default_for_push": True, "priority": 1,
    }
    assert rows["DM2"]["pancake_warehouse_id"] is None and rows["DM2"]["priority"] is None


def test_put_thay_danh_sach(editor, warehouses):
    a, b, c = warehouses
    PancakePosWarehouseLink.objects.create(warehouse=c, pancake_warehouse_id="p-old")
    body = {"links": [
        {"warehouse_id": a.id, "pancake_warehouse_id": "p-1", "pancake_warehouse_name": "ĐM1",
         "is_default_for_push": True, "priority": 1},
        {"warehouse_id": b.id, "pancake_warehouse_id": "p-2", "priority": 2},
    ]}
    res = editor.put(URL, body, format="json")
    assert res.status_code == 200, res.content
    assert dict(PancakePosWarehouseLink.objects.values_list("warehouse_id", "pancake_warehouse_id")) == {
        a.id: "p-1", b.id: "p-2",
    }
    assert {r["code"]: r["pancake_warehouse_id"] for r in res.json()["items"]}["VP"] is None


def test_put_loi_tra_400_khong_doi_du_lieu(editor, warehouses):
    a, b, _ = warehouses
    PancakePosWarehouseLink.objects.create(warehouse=a, pancake_warehouse_id="p-1")
    dup = {"links": [
        {"warehouse_id": a.id, "pancake_warehouse_id": "p-9"},
        {"warehouse_id": b.id, "pancake_warehouse_id": "p-9"},
    ]}
    assert editor.put(URL, dup, format="json").status_code == 400
    unknown = {"links": [{"warehouse_id": 999_999, "pancake_warehouse_id": "p-9"}]}
    assert editor.put(URL, unknown, format="json").status_code == 400
    assert list(PancakePosWarehouseLink.objects.values_list("pancake_warehouse_id", flat=True)) == ["p-1"]


def test_put_rong_xoa_het(editor, warehouses):
    PancakePosWarehouseLink.objects.create(warehouse=warehouses[0], pancake_warehouse_id="p-1")
    assert editor.put(URL, {"links": []}, format="json").status_code == 200
    assert not PancakePosWarehouseLink.objects.exists()


def test_quyen(viewer, warehouses):
    stranger = _client(make_user_with_permissions("wh_stranger", ["orders:view"]))
    assert stranger.get(URL).status_code == 403
    assert stranger.get("/api/pancake-pos/pancake-warehouses").status_code == 403
    body = {"links": [{"warehouse_id": warehouses[0].id, "pancake_warehouse_id": "p-1"}]}
    assert viewer.put(URL, body, format="json").status_code == 403
    assert viewer.delete(URL).status_code in (403, 405)


def test_kho_pancake_qua_client(viewer):
    client = MagicMock()
    client.list_warehouses.return_value = [PancakeWarehouse(id="p-1", name="Đại Mỗ 1")]
    with patch(f"{VIEWS}.build_client", return_value=client):
        res = viewer.get("/api/pancake-pos/pancake-warehouses")
    assert res.json() == {"ok": True, "error": "", "warehouses": [{"id": "p-1", "name": "Đại Mỗ 1"}]}
    client.close.assert_called_once()


def test_kho_pancake_loi_tra_ro_rang(viewer):
    client = MagicMock()
    client.list_warehouses.side_effect = PancakePosApiError("GET /shops/42/warehouses: HTTP 401 — Unauthorized")
    with patch(f"{VIEWS}.build_client", return_value=client):
        res = viewer.get("/api/pancake-pos/pancake-warehouses")
    assert res.status_code == 200 and res.json()["ok"] is False and "401" in res.json()["error"]
