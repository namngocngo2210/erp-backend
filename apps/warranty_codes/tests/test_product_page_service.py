"""Test service dựng payload trang /sp/ và bộ đếm lượt quét.

Bộ đếm dedupe theo (mã, IP, ngày) qua khoá Redis.

KHÔNG gọi `cache.clear()` ở bất kỳ đâu — đó là FLUSHDB trên Redis dùng chung giữa
các lần chạy test song song, và là một quy tắc cứng của repo này. Không cần dọn
cache: mỗi test tạo một `WarrantyCode` mới với `code` ngẫu nhiên, nên khoá
`sp:seen:<code>:...` không bao giờ đụng nhau giữa các test.
"""
from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.catalog.models import Product, ProductProfile
from apps.warranty_codes.application.services import product_page_service
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import ScanResult, WarrantyCode, WarrantyCodeScanLog

pytestmark = pytest.mark.django_db


def _make_code(sku: str | None = None) -> WarrantyCode:
    product = Product.objects.create(name="Bồn ngâm chân", sku=sku) if sku else None
    batch = WarrantyCodeBatchService.create_batch(
        quantity=1, product_id=product.id if product else None, created_by=None
    )
    return WarrantyCode.objects.get(batch=batch)


def test_load_unknown_code_logs_not_found():
    result = product_page_service.load(keyword="KHONGTONTAI", ip="1.2.3.4", user_agent="ua")

    assert result.found is False
    assert WarrantyCodeScanLog.objects.filter(result=ScanResult.NOT_FOUND).count() == 1


def test_load_never_matches_serial():
    """serial in công khai trên tem và sinh tuần tự — tra được là dò được cả dải mã."""
    code = _make_code()

    result = product_page_service.load(keyword=code.serial, ip="1.2.3.4", user_agent="ua")

    assert result.found is False


def test_load_matches_code_and_logs_page_viewed():
    code = _make_code()

    result = product_page_service.load(keyword=code.code.lower(), ip="1.2.3.4", user_agent="ua")

    assert result.found is True
    assert result.code.id == code.id
    assert WarrantyCodeScanLog.objects.filter(result=ScanResult.PAGE_VIEWED).count() == 1


def test_scan_count_dedupes_same_ip_same_day():
    code = _make_code()

    product_page_service.load(keyword=code.code, ip="1.2.3.4", user_agent="ua")
    product_page_service.load(keyword=code.code, ip="1.2.3.4", user_agent="ua")

    code.refresh_from_db()
    assert code.scan_count == 1
    assert code.first_scanned_at is not None
    # log thô vẫn ghi đủ 2 dòng — nhật ký là dấu vết, khác với counter đã dedupe
    assert WarrantyCodeScanLog.objects.filter(result=ScanResult.PAGE_VIEWED).count() == 2


def test_scan_count_increments_for_different_ip():
    code = _make_code()

    product_page_service.load(keyword=code.code, ip="1.2.3.4", user_agent="ua")
    product_page_service.load(keyword=code.code, ip="5.6.7.8", user_agent="ua")

    code.refresh_from_db()
    assert code.scan_count == 2


def test_scan_count_survives_cache_failure(monkeypatch):
    """Redis chết thì trang vẫn phải mở được — thà đếm thiếu còn hơn chặn khách."""
    code = _make_code()

    def _boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache, "add", _boom)
    result = product_page_service.load(keyword=code.code, ip="1.2.3.4", user_agent="ua")

    assert result.found is True
    code.refresh_from_db()
    assert code.scan_count == 0


def test_payload_omits_unpublished_profile_content():
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(
        product=code.product, short_description="Tóm tắt", is_published=False,
        specs=[{"label": "Công suất", "value": "800W"}], visible_sections=["thong_so"],
    )

    payload = product_page_service.build_payload(code)

    assert payload["product"]["sku"] == "BNC02"
    assert payload["product"]["specs"] == []
    assert payload["product"]["short_description"] == ""


def test_payload_prefers_display_name_over_kiotviet_name():
    """Tên KiotViet là chuỗi mô tả kỹ thuật, không in được thành `<h1>`."""
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(
        product=code.product, is_published=True, display_name="Bồn ngâm chân Delax DX-2000",
    )

    payload = product_page_service.build_payload(code)

    assert payload["product"]["name"] == "Bồn ngâm chân Delax DX-2000"


def test_payload_falls_back_to_product_name_when_display_name_empty():
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(product=code.product, is_published=True, display_name="")

    payload = product_page_service.build_payload(code)

    assert payload["product"]["name"] == "Bồn ngâm chân"


def test_payload_keeps_display_name_even_when_unpublished():
    """Trang ẩn vẫn in `<h1>`; đã đặt tên đọc được thì đừng quay lại tên KiotViet."""
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(
        product=code.product, is_published=False, display_name="Bồn ngâm chân Delax DX-2000",
    )

    payload = product_page_service.build_payload(code)

    assert payload["product"]["name"] == "Bồn ngâm chân Delax DX-2000"
    assert payload["product"]["short_description"] == ""


def test_payload_only_returns_sections_marked_visible():
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(
        product=code.product, is_published=True, visible_sections=["thong_so"],
        specs=[{"label": "Công suất", "value": "800W"}],
        highlights=[{"title": "24 bi lăn", "detail": "..."}],
    )

    payload = product_page_service.build_payload(code)

    assert payload["product"]["specs"] == [{"label": "Công suất", "value": "800W"}]
    assert payload["product"]["highlights"] == []


def test_payload_hides_invisible_faqs_and_drops_the_flag():
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(
        product=code.product, is_published=True, visible_sections=["faq"],
        faqs=[{"question": "Q1", "answer": "A1", "visible": True},
              {"question": "Q2", "answer": "A2", "visible": False}],
    )

    payload = product_page_service.build_payload(code)

    assert payload["product"]["faqs"] == [{"question": "Q1", "answer": "A1"}]


def test_payload_product_is_null_when_batch_unassigned():
    code = _make_code()

    payload = product_page_service.build_payload(code)

    assert payload["product"] is None
    assert payload["authentic"] is True
    assert payload["serial"] == code.serial


def test_payload_top_level_keys_are_exactly_these():
    """Chốt chặn rò dữ liệu: thêm trường mới BẮT BUỘC phải sửa test này và cân
    nhắc xem trường đó có nên ra ngoài không. Mirror
    test_track_valid_pair_returns_order_and_journey."""
    code = _make_code(sku="BNC02")

    payload = product_page_service.build_payload(code)

    assert set(payload) == {"code", "serial", "authentic", "scan", "product", "warranty", "company"}
    assert set(payload["scan"]) == {"count", "first_at"}
    assert set(payload["company"]) == {
        "name", "address", "tax_code", "hotline", "zalo_url", "facebook_url",
    }


def test_payload_product_present_without_profile_row():
    """SKU chưa ai viết nội dung — sản phẩm có nhưng KHÔNG có bản ghi ProductProfile.
    Nhánh này khách hàng chạm phải thường xuyên nhất (mọi SKU chưa được biên tập).
    """
    code = _make_code(sku="BNC02")

    payload = product_page_service.build_payload(code)

    assert payload["product"] is not None
    assert payload["product"]["sku"] == "BNC02"
    assert payload["product"]["name"] == code.product.name
    assert payload["product"]["short_description"] == ""
    assert payload["product"]["highlights"] == []
    assert payload["product"]["specs"] == []
    assert payload["product"]["usage_steps"] == []
    assert payload["product"]["troubleshooting"] == []
    assert payload["product"]["faqs"] == []
    assert payload["product"]["videos"] == []
    assert payload["product"]["images"] == []
    assert payload["product"]["audience"] == {}
    assert set(payload["product"]) == {
        "sku", "name", "short_description", "images", "highlights", "specs",
        "audience", "usage_steps", "troubleshooting", "faqs", "videos",
    }


def test_payload_product_keys_are_exactly_these():
    code = _make_code(sku="BNC02")
    ProductProfile.objects.create(product=code.product, is_published=True)

    payload = product_page_service.build_payload(code)

    assert set(payload["product"]) == {
        "sku", "name", "short_description", "images", "highlights", "specs",
        "audience", "usage_steps", "troubleshooting", "faqs", "videos",
    }
