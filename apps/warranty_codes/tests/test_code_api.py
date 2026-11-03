import pytest

from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService

pytestmark = pytest.mark.django_db

BASE = "/api/warranty-codes/codes"


def test_list_codes_with_pagination(auth_client):
    WarrantyCodeBatchService.create_batch(quantity=7, product_id=None, created_by=None)
    r = auth_client.get(f"{BASE}?limit=5&offset=0")
    assert r.status_code == 200, r.data
    assert r.data["total"] == 7
    assert len(r.data["results"]) == 5
    row = r.data["results"][0]
    assert set(row) >= {"serial", "code", "batch_code", "product_name", "product_sku", "status", "created_at"}


def test_search_by_serial_and_code(auth_client):
    b = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    from apps.warranty_codes.models import WarrantyCode

    first = WarrantyCode.objects.filter(batch=b).order_by("seq").first()
    r = auth_client.get(f"{BASE}?search={first.serial}&limit=10&offset=0")
    assert r.data["total"] == 1
    r = auth_client.get(f"{BASE}?search={first.code.lower()}&limit=10&offset=0")  # thường → upper
    assert r.data["total"] == 1


def test_filter_by_product(auth_client, product_factory):
    p = product_factory()
    WarrantyCodeBatchService.create_batch(quantity=2, product_id=p.id, created_by=None)
    WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    r = auth_client.get(f"{BASE}?product_id={p.id}&limit=10&offset=0")
    assert r.data["total"] == 2
    assert r.data["results"][0]["product_sku"] == p.sku


def test_list_ignores_invalid_query_params_instead_of_500(auth_client):
    WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    r = auth_client.get(f"{BASE}?product_id=abc&date_from=xyz&limit=10&offset=0")
    assert r.status_code == 200, r.data
