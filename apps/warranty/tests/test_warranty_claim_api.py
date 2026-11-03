from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.users.models import Permission, User
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_user_with_sections
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus


@pytest.fixture
def warranty_user(db):
    """User có section `warranty` (mọi action) — dùng cho các test happy-path."""
    return make_user_with_sections("warranty-staff", ["warranty"])


@pytest.fixture
def auth_client(warranty_user):
    c = APIClient()
    c.force_authenticate(warranty_user)
    return c


@pytest.mark.django_db
def test_sync_creates_warranty_section_codes():
    sync_permissions()
    codes = set(Permission.objects.filter(section="warranty").values_list("code", flat=True))
    assert codes == {
        "warranty:view",
        "warranty:create",
        "warranty:edit",
        "warranty:delete",
        "warranty:export",
    }


@pytest.mark.django_db
def test_create_claim_returns_journey(auth_client, order_factory):
    order = order_factory()
    r = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["order"]["id"] == order.id
    assert len(r.data["journeys"]) == 1
    assert r.data["journeys"][0]["seq"] == 1


@pytest.mark.django_db
def test_create_duplicate_claim_rejected(auth_client, order_factory):
    order = order_factory()
    WarrantyClaim.objects.create(order=order)
    r = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json")
    assert r.status_code == 409, r.data
    assert WarrantyClaim.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_soft_deleted_claim_still_blocks_new_claim(auth_client, order_factory):
    """OneToOne `order` unique constraint áp dụng cả với claim đã soft-delete.

    create_claim pre-check (`deleted_at__isnull=True`) không thấy conflict nên qua được,
    nhưng INSERT vẫn bắn IntegrityError vì slot order đã bị chiếm → service quy về
    WarrantyClaimExists và endpoint phải trả 409 (KHÔNG phải 500).
    """
    order = order_factory()
    cid = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).data["id"]
    # Soft-delete qua chính endpoint DELETE (deleted_at được set, unique slot vẫn giữ).
    assert auth_client.delete(f"/api/warranty/claims/{cid}").status_code == 204

    r = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json")
    assert r.status_code == 409, r.data


@pytest.mark.django_db
def test_create_for_missing_order_rejected(auth_client):
    r = auth_client.post("/api/warranty/claims", {"order_id": 999999}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_complete_endpoint(auth_client, order_factory):
    order = order_factory()
    cid = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).data["id"]
    r = auth_client.post(f"/api/warranty/claims/{cid}/complete")
    assert r.status_code == 200, r.data
    assert r.data["status"] == int(WarrantyClaimStatus.COMPLETED)
    assert r.data["completed_at"]


@pytest.mark.django_db
def test_complete_endpoint_404_for_missing_claim(auth_client):
    r = auth_client.post("/api/warranty/claims/999999/complete")
    assert r.status_code == 404


@pytest.mark.django_db
def test_patch_status_completed_directly_stamps_completed_at(auth_client, order_factory):
    """NV chọn tay sang Hoàn thành qua PATCH → 200 + stamp completed_at (flow mới)."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}",
        {"status": int(WarrantyClaimStatus.COMPLETED)},
        format="json",
    )
    assert r.status_code == 200, r.data
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None


@pytest.mark.django_db
def test_add_and_patch_journey(auth_client, order_factory):
    order = order_factory()
    claim = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).data
    r = auth_client.post(f"/api/warranty/claims/{claim['id']}/journeys")
    assert r.status_code == 201, r.data
    assert r.data["seq"] == 2  # seq=1 đã được create_claim tự sinh
    jid = r.data["id"]

    p = auth_client.patch(
        f"/api/warranty/journeys/{jid}", {"new_tracking_code": "GHN9"}, format="json"
    )
    assert p.status_code == 200, p.data
    assert p.data["status"] == "created"
    assert p.data["new_tracking_code"] == "GHN9"


@pytest.mark.django_db
def test_delete_journey(auth_client, order_factory):
    order = order_factory()
    claim = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).data
    added = auth_client.post(f"/api/warranty/claims/{claim['id']}/journeys")
    r = auth_client.delete(f"/api/warranty/journeys/{added.data['id']}")
    assert r.status_code == 204


@pytest.mark.django_db
def test_journey_endpoints_404_when_missing(auth_client):
    assert auth_client.post("/api/warranty/claims/999999/journeys").status_code == 404
    assert (
        auth_client.patch(
            "/api/warranty/journeys/999999", {"note": "x"}, format="json"
        ).status_code
        == 404
    )
    assert auth_client.delete("/api/warranty/journeys/999999").status_code == 404


@pytest.mark.django_db
def test_list_filter_and_search(auth_client, order_factory):
    order = order_factory(shipping_code="VDX9", order_code="ODX9")
    WarrantyClaim.objects.create(order=order, status=WarrantyClaimStatus.COMPLETED)
    other = order_factory(shipping_code="VDZZ", order_code="ODZZ")
    WarrantyClaim.objects.create(order=other, status=WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM)

    r = auth_client.get("/api/warranty/claims?limit=50&offset=0")
    assert r.status_code == 200
    assert r.data["total"] == 2

    r = auth_client.get(
        f"/api/warranty/claims?status={int(WarrantyClaimStatus.COMPLETED)}&limit=50&offset=0"
    )
    assert r.data["total"] == 1
    assert r.data["results"][0]["order"]["id"] == order.id

    r = auth_client.get("/api/warranty/claims?q=VDZZ&limit=50&offset=0")
    assert r.data["total"] == 1
    assert r.data["results"][0]["order"]["id"] == other.id


@pytest.mark.django_db
def test_patch_claim(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}",
        {"status": int(WarrantyClaimStatus.CANCELLED)},
        format="json",
    )
    assert r.status_code == 200, r.data
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.CANCELLED


@pytest.mark.django_db
def test_soft_delete_claim(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.delete(f"/api/warranty/claims/{claim.id}")
    assert r.status_code == 204
    claim.refresh_from_db()
    assert claim.deleted_at is not None
    # excluded from list after delete
    r = auth_client.get("/api/warranty/claims?limit=50&offset=0")
    assert r.data["total"] == 0


@pytest.mark.django_db
def test_patch_activation_date_returns_expiry_and_days_left(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}",
        {"activation_date": "2026-01-15"},
        format="json",
    )
    assert r.status_code == 200, r.data
    assert r.data["activation_date"] == "2026-01-15"
    assert r.data["warranty_months"] == 24
    assert str(r.data["expiry_date"]) == "2028-01-15"
    assert isinstance(r.data["warranty_days_left"], int)


@pytest.mark.django_db
def test_patch_warranty_months_changes_expiry(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}",
        {"activation_date": "2026-01-15", "warranty_months": 12},
        format="json",
    )
    assert r.status_code == 200, r.data
    assert str(r.data["expiry_date"]) == "2027-01-15"


@pytest.mark.django_db
def test_patch_invalid_warranty_months_rejected(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}", {"warranty_months": 0}, format="json"
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_clear_activation_date(auth_client, order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory(), activation_date=date(2026, 1, 15))
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}", {"activation_date": None}, format="json"
    )
    assert r.status_code == 200, r.data
    assert r.data["activation_date"] is None
    assert r.data["expiry_date"] is None
    assert r.data["warranty_days_left"] is None


@pytest.mark.django_db
def test_outsider_forbidden(order_factory):
    outsider = User.objects.create_user(username="no-warranty", password="x")
    order = order_factory()
    claim = WarrantyClaim.objects.create(order=order)
    c = APIClient()
    c.force_authenticate(outsider)

    assert c.get("/api/warranty/claims?limit=50&offset=0").status_code == 403
    assert (
        c.post("/api/warranty/claims", {"order_id": order.id}, format="json").status_code == 403
    )
    assert c.post(f"/api/warranty/claims/{claim.id}/complete").status_code == 403
    assert c.post(f"/api/warranty/claims/{claim.id}/journeys").status_code == 403
