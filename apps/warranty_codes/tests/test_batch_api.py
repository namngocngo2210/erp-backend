import pytest
from rest_framework.test import APIClient

from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyCodeBatch

pytestmark = pytest.mark.django_db

BASE = "/api/warranty-codes/batches"


def test_requires_section(db):
    other = make_user_with_sections("other-staff", ["orders"])
    client = APIClient()
    client.force_authenticate(other)
    assert client.get(f"{BASE}?limit=10&offset=0").status_code == 403


def test_create_and_list_batch(auth_client, product_factory):
    r = auth_client.post(BASE, {"quantity": 5, "product_id": None}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["quantity"] == 5
    assert r.data["status"] == "reserved"
    assert r.data["start_serial"] == "DX-000001"

    r = auth_client.get(f"{BASE}?limit=10&offset=0")
    assert r.status_code == 200
    assert r.data["total"] == 1
    assert r.data["results"][0]["batch_code"].startswith("LOT-")


def test_create_validates_quantity(auth_client):
    r = auth_client.post(BASE, {"quantity": 0}, format="json")
    assert r.status_code == 400
    r = auth_client.post(BASE, {"quantity": 1, "start_seq": 0}, format="json")
    assert r.status_code == 400


def test_create_with_start_seq(auth_client):
    r = auth_client.post(BASE, {"quantity": 3, "start_seq": 20001, "product_id": None}, format="json")
    assert r.status_code == 201, r.data
    assert (r.data["start_seq"], r.data["end_seq"]) == (20001, 20003)
    assert r.data["start_serial"] == "DX-020001"
    assert r.data["is_generating"] is False
    assert r.data["generated_count"] == 3
    # Trùng dải → 400 kèm tên lô đang chiếm.
    r2 = auth_client.post(BASE, {"quantity": 1, "start_seq": 20002}, format="json")
    assert r2.status_code == 400
    assert r.data["batch_code"] in r2.data["detail"]


def test_create_no_upper_limit_goes_background(auth_client, settings, monkeypatch, django_capture_on_commit_callbacks):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 5
    from apps.warranty_codes import tasks

    monkeypatch.setattr(tasks.generate_batch_codes, "delay", lambda batch_id: None)
    with django_capture_on_commit_callbacks(execute=True):
        r = auth_client.post(BASE, {"quantity": 150_000}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["is_generating"] is True
    assert r.data["generated_count"] == 0
    assert r.data["quantity"] == 150_000


def test_list_filters_by_status_and_product(auth_client, product_factory):
    p = product_factory()
    WarrantyCodeBatchService.create_batch(quantity=1, product_id=p.id, created_by=None)
    WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    r = auth_client.get(f"{BASE}?status=assigned&limit=10&offset=0")
    assert r.data["total"] == 1
    r = auth_client.get(f"{BASE}?product_id={p.id}&limit=10&offset=0")
    assert r.data["total"] == 1
    assert r.data["results"][0]["product_sku"] == p.sku
    r = auth_client.get(f"{BASE}?q=LOT-&limit=10&offset=0")
    assert r.data["total"] == 2


def test_assign_endpoint(auth_client, product_factory):
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    r = auth_client.post(f"{BASE}/{b.id}/assign", {"product_id": p.id}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["status"] == "assigned"
    assert r.data["product_name"] == p.name


def test_split_endpoint(auth_client):
    b = WarrantyCodeBatchService.create_batch(quantity=10, product_id=None, created_by=None)
    r = auth_client.post(f"{BASE}/{b.id}/split", {"cut_seq": b.start_seq + 4}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["parent"]["quantity"] == 5
    assert r.data["child"]["quantity"] == 5
    assert WarrantyCodeBatch.objects.count() == 2


def test_split_invalid_cut_returns_400(auth_client):
    b = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    r = auth_client.post(f"{BASE}/{b.id}/split", {"cut_seq": b.end_seq}, format="json")
    assert r.status_code == 400


def test_list_ignores_invalid_query_params_instead_of_500(auth_client):
    r = auth_client.get(f"{BASE}?product_id=abc&date_from=xyz&limit=10&offset=0")
    assert r.status_code == 200, r.data


def test_create_batch_requires_create_permission(product_factory):
    view_only = make_user_with_permissions("wc-viewer", ["warranty-codes:view"])
    client = APIClient()
    client.force_authenticate(view_only)

    r = client.get(f"{BASE}?limit=10&offset=0")
    assert r.status_code == 200, r.data

    r = client.post(BASE, {"quantity": 5, "product_id": None}, format="json")
    assert r.status_code == 403, r.data
    # Audit 2026-08-19 N2: gate chuyen len decorator (require_method_permission)
    # nen 403 mang message chung; check ban trong view van con (defense in depth).
    assert r.data["detail"] == "Bạn không có quyền thực hiện hành động này."

    b = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    r = client.post(f"{BASE}/{b.id}/assign", {"product_id": product_factory().id}, format="json")
    assert r.status_code == 403, r.data

    r = client.post(f"{BASE}/{b.id}/split", {"cut_seq": b.start_seq}, format="json")
    assert r.status_code == 403, r.data
    # `GET {BASE}/{id}/export` (endpoint đồng bộ) đã xoá ở Task 18 cùng
    # `export_batch` — nhánh "thiếu quyền export bị chặn" giờ khoá ở
    # `test_batch_export_definition.py::test_check_permission_denies_without_
    # export_right` (WarrantyCodeBatchExport.check_permission raise
    # PermissionDenied), không còn endpoint HTTP nào để assert 403 ở đây.
