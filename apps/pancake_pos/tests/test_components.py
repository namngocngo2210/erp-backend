"""Linh kiện lên Pancake (two-way-sync §6b, thay luật !1508): có cột
`products.product_type` → linh kiện đủ điều kiện, đẩy ẩn + danh mục "Linh kiện";
không có cột → bỏ nhánh, ghi báo cáo; cờ PANCAKE_POS_INCLUDE_COMPONENTS tắt → loại."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.catalog.models import Product
from apps.pancake_pos.application.services.factory import build_product_sync_service
from apps.pancake_pos.application.services.product_sync_service import (
    COMPONENT_CATEGORY_KEY,
    ProductSyncService,
)
from apps.pancake_pos.domain.eligibility import REASON_COMPONENT, ineligible_reason
from apps.pancake_pos.domain.entities import PRODUCT_TYPE_COMPONENT, PRODUCT_TYPE_GOODS, CatalogProduct
from apps.pancake_pos.domain.mapping import build_group_payload, group_products
from apps.pancake_pos.infrastructure.catalog_reader import SqlCatalogReader, products_have_product_type
from apps.pancake_pos.models import PancakePosCategory, PancakePosSyncRun
from apps.pancake_pos.tests.fakes import FakePancake, FakeReader
from apps.sysconfig import override_sysconfig

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


def _cp(pid: int, sku: str, *, group: str | None = None, ptype: str | None = None, **kw) -> CatalogProduct:
    kw.setdefault("has_stock", True)
    return CatalogProduct(id=pid, sku=sku, name=f"SP {sku}", group_code=group, product_type=ptype, **kw)


# ── domain (thuần) ──────────────────────────────────────────────────────────
def test_linh_kien_du_dieu_kien_mac_dinh_loai_khi_tat_co():
    lk = _cp(1, "LK01", ptype=PRODUCT_TYPE_COMPONENT)
    assert ineligible_reason(lk, now=NOW) is None
    assert ineligible_reason(lk, now=NOW, include_components=False) == REASON_COMPONENT
    # Luật tồn/bán vẫn áp cho linh kiện
    assert ineligible_reason(_cp(2, "LK02", ptype=PRODUCT_TYPE_COMPONENT, has_stock=False), now=NOW) is not None
    # Hàng hoá / chưa có cột không bị ảnh hưởng bởi cờ
    for ptype in (PRODUCT_TYPE_GOODS, None):
        assert ineligible_reason(_cp(3, "HH01", ptype=ptype), now=NOW, include_components=False) is None


def test_payload_linh_kien_an_va_gan_danh_muc_chi_khi_ca_nhom_la_linh_kien():
    [only_lk] = group_products([_cp(1, "LK01", ptype=PRODUCT_TYPE_COMPONENT)])
    body = build_group_payload(only_lk, default_weight_g=500, component_category_id="77").to_request()["product"]
    assert body["category_ids"] == [77]
    assert body["variations"][0]["is_hidden"] is True

    [mixed] = group_products([
        _cp(2, "GRP-A", group="GRP", ptype=PRODUCT_TYPE_GOODS),
        _cp(3, "GRP-LK", group="GRP", ptype=PRODUCT_TYPE_COMPONENT),
    ])
    body = build_group_payload(mixed, default_weight_g=500, component_category_id="77").to_request()["product"]
    assert "category_ids" not in body
    assert {v["custom_id"]: v["is_hidden"] for v in body["variations"]} == {"GRP-A": False, "GRP-LK": True}


def test_hash_hang_hoa_khong_doi_so_voi_truoc_khi_co_cot():
    """Không có cột (None) và `goods` cho cùng payload → bật MR !1504 không kéo theo PUT hàng loạt."""
    [before] = group_products([_cp(1, "HH01")])
    [after] = group_products([_cp(1, "HH01", ptype=PRODUCT_TYPE_GOODS)])
    assert (
        build_group_payload(before, default_weight_g=500).hash
        == build_group_payload(after, default_weight_g=500, component_category_id="77").hash
    )


# ── service ─────────────────────────────────────────────────────────────────
pytestmark_db = pytest.mark.django_db


def _products(*items: tuple[str, str | None]) -> list[CatalogProduct]:
    out = []
    for sku, ptype in items:
        product = Product.objects.create(sku=sku, name=f"SP {sku}", price=Decimal(100000))
        out.append(_cp(product.id, sku, ptype=ptype))
    return out


def _service(pancake: FakePancake, products: list[CatalogProduct], *, supported: bool, include: bool = True):
    reader = FakeReader(products)
    reader.product_type_supported = supported
    return ProductSyncService(gateway=pancake, catalog_reader=reader, include_components=include, clock=lambda: NOW)


@pytestmark_db
def test_khong_co_cot_bo_qua_nhanh_linh_kien_va_ghi_bao_cao():
    pancake = FakePancake()
    report = _service(pancake, _products(("HH01", None)), supported=False).full_sync(dry_run=False)
    assert report.components == {"status": "unsupported"}
    assert ("list_categories", None) not in pancake.calls
    run = PancakePosSyncRun.objects.get(id=report.run_id)
    assert run.report["components"] == {"status": "unsupported"}


@pytestmark_db
def test_co_cot_tao_danh_muc_mot_lan_roi_dung_cache():
    pancake = FakePancake()
    products = _products(("LK01", PRODUCT_TYPE_COMPONENT), ("HH01", PRODUCT_TYPE_GOODS))
    report = _service(pancake, products, supported=True).full_sync(dry_run=False)

    assert report.components["status"] == "included" and report.components["eligible"] == 1
    assert report.components["category"] == "created"
    category_id = report.components["category_id"]
    assert PancakePosCategory.objects.get(key=COMPONENT_CATEGORY_KEY).pancake_category_id == category_id
    created = {c[1]["product"]["custom_id"]: c[1]["product"] for c in pancake.write_calls() if c[0] == "create"}
    assert created["LK01"]["category_ids"] == [int(category_id)]
    assert created["LK01"]["variations"][0]["is_hidden"] is True
    assert "category_ids" not in created["HH01"] and created["HH01"]["variations"][0]["is_hidden"] is False

    # Lượt 2: lấy từ cache, không gọi danh mục nữa, không tạo thêm
    pancake.calls.clear()
    second = _service(pancake, products, supported=True).full_sync(dry_run=False)
    assert ("list_categories", None) not in pancake.calls
    assert [c for c in pancake.calls if c[0] in ("create", "create_category")] == []
    assert second.counters.skipped == 2


@pytestmark_db
def test_danh_muc_da_co_tren_pancake_thi_dung_lai():
    pancake = FakePancake()
    pancake.categories = [{"id": "55", "name": "linh KIỆN", "parent_id": ""}]
    report = _service(pancake, _products(("LK01", PRODUCT_TYPE_COMPONENT)), supported=True).full_sync(dry_run=False)
    assert report.components["category_id"] == "55"
    assert not [c for c in pancake.calls if c[0] == "create_category"]


@pytestmark_db
def test_dry_run_khong_tao_danh_muc():
    pancake = FakePancake()
    report = _service(pancake, _products(("LK01", PRODUCT_TYPE_COMPONENT)), supported=True).full_sync(dry_run=True)
    assert report.components["category"] == "would_create"
    assert pancake.write_calls() == []
    assert not PancakePosCategory.objects.exists()
    [sample] = report.samples
    assert sample["request"]["product"]["variations"][0]["is_hidden"] is True


@pytestmark_db
def test_tat_co_thi_loai_linh_kien():
    pancake = FakePancake()
    products = _products(("LK01", PRODUCT_TYPE_COMPONENT), ("HH01", PRODUCT_TYPE_GOODS))
    report = _service(pancake, products, supported=True, include=False).full_sync(dry_run=False)
    assert report.components == {"status": "excluded"}
    assert report.ineligible[REASON_COMPONENT] == 1
    assert {c[1]["product"]["custom_id"] for c in pancake.write_calls() if c[0] == "create"} == {"HH01"}


@pytestmark_db
def test_loi_danh_muc_khong_chan_luot():
    pancake = FakePancake()

    def boom(*_a, **_kw):
        from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosApiError

        raise PancakePosApiError("HTTP 500")

    pancake.list_categories = boom  # type: ignore[method-assign]
    report = _service(pancake, _products(("LK01", PRODUCT_TYPE_COMPONENT)), supported=True).full_sync(dry_run=False)
    assert "HTTP 500" in report.components["category_error"]
    assert report.counters.created == 1  # vẫn đẩy (ẩn), lượt sau gắn danh mục


@pytestmark_db
def test_factory_doc_co_include_components():
    with override_sysconfig(PANCAKE_POS_API_KEY="k", PANCAKE_POS_SHOP_ID=1, PANCAKE_POS_INCLUDE_COMPONENTS=False):
        assert build_product_sync_service(dry_run=True)._include_components is False


# ── reader: introspection cột ───────────────────────────────────────────────
@pytestmark_db
def test_reader_khong_co_cot_tra_none():
    # Sau khi MR !1504 merge, cột đã có thật trong schema test → ca "chưa có cột" không còn.
    if products_have_product_type():
        pytest.skip("products.product_type đã tồn tại (MR !1504 đã merge)")
    Product.objects.create(sku="HH01", name="Hàng", price=Decimal(1))
    reader = SqlCatalogReader()
    assert reader.has_product_type() is False
    [row] = reader.list_candidates(sold_since=timezone.now() - timedelta(days=180))
    assert row.product_type is None


@pytestmark_db
def test_reader_co_cot_doc_gia_tri():
    """Mô phỏng MR !1504 đã deploy: thêm cột trong transaction test (DDL PG rollback được)."""
    # ALTER trước mọi INSERT (PG chặn ALTER khi còn trigger FK hoãn trong transaction).
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS product_type varchar(20) NOT NULL DEFAULT 'goods'")
    lk = Product.objects.create(sku="LK01", name="Linh kiện", price=Decimal(1))
    with connection.cursor() as cur:
        cur.execute("UPDATE products SET product_type = 'component' WHERE id = %s", [lk.id])
    reader = SqlCatalogReader()
    assert reader.has_product_type() is True
    rows = {p.sku: p for p in reader.list_candidates(sold_since=timezone.now() - timedelta(days=180))}
    assert rows["LK01"].is_component
