"""Test bulk-delete phiếu bảo hành: `POST /api/warranty/claims/bulk-delete`.

Tách khỏi `test_claim_bulk_export.py` (Task 18 — xoá endpoint export đồng bộ
`GET /api/warranty/claims/export`, xem
docs/.superpowers/sdd/2026-08-06-async-export-jobs/task-18-report.md). Phần
export đã chuyển hẳn sang `apps/warranty/tests/test_warranty_export_definition.py`
(engine chạy nền `apps.exports`) — file này CHỈ còn giữ bulk-delete, một endpoint
không liên quan tới luồng export nên không thuộc phạm vi Task 18.
"""
import pytest
from rest_framework.test import APIClient

from apps.users.models import User
from apps.users.tests.helpers import make_user_with_sections
from apps.warranty.models import WarrantyClaim


@pytest.fixture
def warranty_user(db):
    """User có section `warranty` (mọi action) — mirror conftest của test_warranty_claim_api."""
    return make_user_with_sections("warranty-staff-2", ["warranty"])


@pytest.fixture
def auth_client(warranty_user):
    c = APIClient()
    c.force_authenticate(warranty_user)
    return c


@pytest.mark.django_db
def test_bulk_delete_soft_deletes_selected_ids(auth_client, order_factory):
    order1 = order_factory()
    claim1 = WarrantyClaim.objects.create(order=order1)
    order2 = order_factory()
    claim2 = WarrantyClaim.objects.create(order=order2)
    order3 = order_factory()
    claim3 = WarrantyClaim.objects.create(order=order3)

    r = auth_client.post(
        "/api/warranty/claims/bulk-delete",
        {"ids": [claim1.id, claim2.id]},
        format="json",
    )
    assert r.status_code == 200, r.data
    assert r.data["deleted"] == 2

    claim1.refresh_from_db()
    claim2.refresh_from_db()
    claim3.refresh_from_db()
    assert claim1.deleted_at is not None
    assert claim2.deleted_at is not None
    assert claim3.deleted_at is None


@pytest.mark.django_db
def test_bulk_delete_ignores_already_deleted_and_missing_ids(auth_client, order_factory):
    order = order_factory()
    claim = WarrantyClaim.objects.create(order=order)
    auth_client.delete(f"/api/warranty/claims/{claim.id}")

    r = auth_client.post(
        "/api/warranty/claims/bulk-delete",
        {"ids": [claim.id, 999999]},
        format="json",
    )
    assert r.status_code == 200, r.data
    assert r.data["deleted"] == 0


@pytest.mark.django_db
def test_bulk_delete_rejects_empty_ids(auth_client):
    r = auth_client.post("/api/warranty/claims/bulk-delete", {"ids": []}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_bulk_delete_forbidden_for_outsider(order_factory):
    """Outsider (không quyền `warranty` nào) bị 403 khi gọi bulk-delete phiếu bảo hành.

    Tách từ `test_export_and_bulk_delete_forbidden_for_outsider` gốc (2 assertion
    export + bulk-delete) — nhánh export giờ đã được `test_check_permission_
    denies_without_export_right` (test_warranty_export_definition.py) khoá riêng;
    endpoint export đồng bộ `GET /api/warranty/claims/export` đã xoá ở Task 18.
    """
    outsider = User.objects.create_user(username="no-warranty-2", password="x")
    order = order_factory()
    claim = WarrantyClaim.objects.create(order=order)
    c = APIClient()
    c.force_authenticate(outsider)

    assert (
        c.post("/api/warranty/claims/bulk-delete", {"ids": [claim.id]}, format="json").status_code
        == 403
    )
