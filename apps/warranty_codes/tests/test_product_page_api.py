"""Test endpoint public của trang sản phẩm /sp/<mã>.

Không dọn cache giữa các test (và tuyệt đối không `cache.clear()` — FLUSHDB trên
Redis dùng chung): mỗi test tạo mã mới nên khoá dedupe không đụng nhau.
"""
from __future__ import annotations

import io

import pytest

from apps.catalog.models import Product, ProductProfile, ProductProfileImage
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import ScanResult, WarrantyCode, WarrantyCodeScanLog
from apps.warranty_codes.transport.portal_throttles import PortalMediaThrottle
from apps.warranty_codes.transport.portal_views import product_image

pytestmark = pytest.mark.django_db


def _make_code(sku: str | None = None) -> WarrantyCode:
    product = Product.objects.create(name="Bồn ngâm chân", sku=sku) if sku else None
    batch = WarrantyCodeBatchService.create_batch(
        quantity=1, product_id=product.id if product else None, created_by=None
    )
    return WarrantyCode.objects.get(batch=batch)


def _url(code: str) -> str:
    return f"/api/warranty-portal/products/{code}"


def test_unknown_code_returns_404_with_counterfeit_hint(public_client):
    response = public_client.get(_url("KHONGCOTHAT"))

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert WarrantyCodeScanLog.objects.filter(result=ScanResult.NOT_FOUND).count() == 1
    # Nhánh 404 cũng ghi log (tác dụng phụ) nên cũng không được cache — như nhánh 200.
    assert "no-store" in response["Cache-Control"]


def test_serial_is_not_accepted(public_client):
    code = _make_code()

    response = public_client.get(_url(code.serial))

    assert response.status_code == 404


def test_valid_code_returns_payload_and_no_store(public_client):
    code = _make_code(sku="BNC02")

    response = public_client.get(_url(code.code))

    assert response.status_code == 200
    body = response.json()
    assert body["serial"] == code.serial
    assert body["authentic"] is True
    assert body["product"]["sku"] == "BNC02"
    # Endpoint CÓ tác dụng phụ (tăng counter, ghi log) nên không được cache
    assert "no-store" in response["Cache-Control"]


def test_unassigned_batch_returns_null_product(public_client):
    code = _make_code()

    body = public_client.get(_url(code.code)).json()

    assert body["product"] is None
    assert body["authentic"] is True


def test_response_top_level_keys_are_exactly_these(public_client):
    """Chốt chặn rò dữ liệu ở tầng HTTP — song song với test cùng tên ở tầng service."""
    code = _make_code(sku="BNC02")

    body = public_client.get(_url(code.code)).json()

    assert set(body) == {"code", "serial", "authentic", "scan", "product", "warranty", "company"}


def test_activated_code_returns_warranty_in_activate_shape(public_client):
    code = _make_code(sku="BNC02")
    public_client.post(
        "/api/warranty-portal/activate",
        {"keyword": code.code, "phone": "0901234567", "channel": "web"},
        format="json",
    )

    body = public_client.get(_url(code.code)).json()

    assert set(body["warranty"]) == {
        "serial", "code", "product_sku", "product_name",
        "activated_at", "expires_at", "days_left", "months",
    }


def test_image_endpoint_serves_bytes_with_immutable_cache(public_client, monkeypatch):
    code = _make_code(sku="BNC02")
    profile = ProductProfile.objects.create(product=code.product, is_published=True)
    image = ProductProfileImage.objects.create(
        profile=profile, object_key="a.jpg", content_type="image/jpeg"
    )
    payload = b"\xff\xd8\xffbinary"
    monkeypatch.setattr(
        "apps.orders.infrastructure.product_storage.get_object_stream",
        lambda key: (io.BytesIO(payload), "image/jpeg", len(payload)),
    )

    response = public_client.get(f"/api/warranty-portal/product-images/{image.id}")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert "immutable" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Security-Policy"] == "default-src 'none'; img-src 'self'"
    assert response["Content-Length"] == str(len(payload))


def test_image_endpoint_uses_dedicated_media_throttle():
    """Chốt chặn hồi quy: nếu ai đó xoá @throttle_classes khỏi product_image, DRF sẽ
    âm thầm rơi về DEFAULT_THROTTLE_CLASSES (bucket `anon` dùng chung toàn hệ thống,
    600/giờ/IP) — một trang sản phẩm kéo 8 ảnh sẽ cạn bucket đó sau vài chục lượt
    xem và làm 429 lây sang những endpoint public không liên quan."""
    assert product_image.cls.throttle_classes == [PortalMediaThrottle]


def test_image_endpoint_404_for_unknown_id(public_client):
    response = public_client.get("/api/warranty-portal/product-images/999999")

    assert response.status_code == 404


def test_image_of_unpublished_profile_is_not_served(public_client):
    """Ảnh của nội dung chưa công bố không được phục vụ — nếu không, đặt
    is_published=False sẽ không thực sự giấu được gì."""
    code = _make_code(sku="BNC02")
    profile = ProductProfile.objects.create(product=code.product, is_published=False)
    image = ProductProfileImage.objects.create(
        profile=profile, object_key="a.jpg", content_type="image/jpeg"
    )

    response = public_client.get(f"/api/warranty-portal/product-images/{image.id}")

    assert response.status_code == 404


def test_image_endpoint_streams_async(public_client, monkeypatch, read_streaming_response):
    """Ảnh phải stream đường async — xem lý do đầy đủ ở
    apps/exports/tests/test_api.py::test_download_streams_async_without_buffering_whole_file.

    Riêng endpoint này còn nặng hơn: nó CÔNG KHAI (AllowAny) và dung lượng ảnh
    chưa bị cap, nên mỗi request gom trọn object vào RAM là một đòn khuếch đại
    bộ nhớ rất rẻ tiền cho phía tấn công.
    """
    code = _make_code(sku="BNC03")
    profile = ProductProfile.objects.create(product=code.product, is_published=True)
    image = ProductProfileImage.objects.create(
        profile=profile, object_key="a.jpg", content_type="image/jpeg"
    )
    payload = b"\xff\xd8\xff" + b"x" * 200_000
    monkeypatch.setattr(
        "apps.orders.infrastructure.product_storage.get_object_stream",
        lambda key: (io.BytesIO(payload), "image/jpeg", len(payload)),
    )

    response = public_client.get(f"/api/warranty-portal/product-images/{image.id}")

    assert response.status_code == 200
    assert response.is_async is True
    assert read_streaming_response(response) == payload
