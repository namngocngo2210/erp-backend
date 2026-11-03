"""`ProductSyncService.full_sync` với Pancake giả: idempotent, liên kết hàng có sẵn,
PUT thêm mẫu mã, xung đột, lỗi cô lập theo nhóm, dry-run không ghi."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.pancake_pos.application.services.product_sync_service import ProductSyncService
from apps.pancake_pos.domain.entities import CatalogProduct
from apps.pancake_pos.models import (
    LinkStatus,
    PancakePosProductLink,
    PancakePosSyncRun,
    PancakePosVariationLink,
)
from apps.pancake_pos.tests.fakes import FakePancake, FakeReader

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _catalog(sku: str, name: str, group: str | None = None, **kw) -> CatalogProduct:
    product = Product.objects.create(sku=sku, name=name, price=Decimal(kw.pop("price", 100000)))
    kw.setdefault("has_stock", True)
    return CatalogProduct(id=product.id, sku=sku, name=name, group_code=group, price=product.price, **kw)


def _service(pancake: FakePancake, products: list[CatalogProduct]) -> ProductSyncService:
    return ProductSyncService(gateway=pancake, catalog_reader=FakeReader(products), clock=lambda: NOW)


@pytest.fixture
def ohi():
    return [
        _catalog("OHI01-DEN", "Ghế massage toàn thân - Đen", "OHI01"),
        _catalog("OHI01-TRANG", "Ghế massage toàn thân - Trắng", "OHI01"),
    ]


def test_tao_moi_roi_chay_lai_khong_tao_them(ohi):
    goi = _catalog("GOI01", "Gối massage cổ")
    pancake = FakePancake()
    service = _service(pancake, [*ohi, goi])

    first = service.full_sync(dry_run=False)
    assert first.counters.as_dict() == {"created": 3, "updated": 0, "linked": 0, "skipped": 0, "conflicts": 0, "errors": 0, "hidden": 0}
    assert len(pancake.products) == 2
    creates = [c for c in pancake.write_calls() if c[0] == "create"]
    assert {c[1]["product"]["custom_id"] for c in creates} == {"OHI01", "GOI01"}
    # POST không trả id → phải GET lại theo custom_id
    assert ("get", "OHI01") in pancake.calls and ("get", "GOI01") in pancake.calls

    links = {v.product_id: v for v in PancakePosVariationLink.objects.all()}
    assert all(v.status == LinkStatus.SYNCED and v.pancake_variation_id for v in links.values())
    assert PancakePosProductLink.objects.get(group_key="OHI01").pancake_product_id in pancake.products
    assert PancakePosProductLink.objects.filter(group_key="sku:GOI01").exists()

    pancake.calls.clear()
    second = service.full_sync(dry_run=False)
    assert second.counters.skipped == 3
    assert second.counters.created == second.counters.updated == 0
    assert pancake.write_calls() == []
    assert len(pancake.products) == 2
    assert PancakePosSyncRun.objects.count() == 2
    run = PancakePosSyncRun.objects.get(id=second.run_id)
    assert run.mode == "full" and run.skipped == 3 and run.finished_at is not None


def test_doi_gia_thi_put_cap_nhat(ohi):
    pancake = FakePancake()
    _service(pancake, ohi).full_sync(dry_run=False)
    changed = [replace(ohi[0], price=Decimal(99000)), ohi[1]]
    pancake.calls.clear()
    report = _service(pancake, changed).full_sync(dry_run=False)
    assert report.counters.updated == 2
    (call,) = pancake.write_calls()
    assert call[0] == "update"
    variations = call[1][1]["product"]["variations"]
    assert all(v.get("id") for v in variations)  # sửa theo id, không thêm mới
    assert len(next(iter(pancake.products.values()))["variations"]) == 2


def test_lien_ket_san_pham_co_san_tren_pancake(ohi):
    pancake = FakePancake()
    pid = pancake.seed("KHAC-MA", [
        {"id": "va", "custom_id": "", "barcode": "ohi01-den"},
        {"id": "vb", "custom_id": "OHI01-TRANG", "barcode": ""},
    ], product_id="shop-p1")
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.linked == 2 and report.counters.created == 0
    (call,) = pancake.write_calls()
    assert call[0] == "update" and call[1][0] == pid
    assert PancakePosProductLink.objects.get(group_key="OHI01").pancake_product_id == pid
    assert set(PancakePosVariationLink.objects.values_list("pancake_variation_id", flat=True)) == {"va", "vb"}
    assert len(pancake.products) == 1  # không tạo trùng


def test_them_mau_ma_vao_san_pham_co_san_bang_put(ohi):
    pancake = FakePancake()
    pid = pancake.seed("OHI01", [{"id": "va", "custom_id": "OHI01-DEN"}])
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.linked == 1 and report.counters.created == 1
    (call,) = pancake.write_calls()
    assert call[0] == "update" and call[1][0] == pid
    sent = call[1][1]["product"]["variations"]
    assert [v.get("id") for v in sent] == ["va", None]
    new_link = PancakePosVariationLink.objects.get(product_id=ohi[1].id)
    assert new_link.status == LinkStatus.SYNCED
    assert new_link.pancake_variation_id == pancake.products[pid]["variations"][1]["id"]


def test_san_pham_pancake_theo_custom_id_nhom_khi_chua_khop_sku(ohi):
    pancake = FakePancake()
    pid = pancake.seed("ohi01", [{"id": "vx", "custom_id": "OHI01-XANH"}])
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.created == 2
    assert pancake.write_calls()[0][0] == "update" and pancake.write_calls()[0][1][0] == pid
    assert len(pancake.products) == 1


def test_sku_khop_nhieu_mau_ma_la_xung_dot(ohi):
    pancake = FakePancake()
    pancake.seed("A", [{"id": "v1", "custom_id": "OHI01-DEN"}])
    pancake.seed("B", [{"id": "v2", "barcode": "OHI01-DEN"}])
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.conflicts == 1
    assert report.conflicts[0]["sku"] == "OHI01-DEN"
    conflict = PancakePosVariationLink.objects.get(product_id=ohi[0].id)
    assert conflict.status == LinkStatus.CONFLICT and "2 mẫu mã" in conflict.last_error
    assert conflict.pancake_variation_id is None
    # SKU còn lại vẫn được tạo, không gửi SKU xung đột
    creates = [c for c in pancake.write_calls() if c[0] == "create"]
    assert [v["custom_id"] for v in creates[0][1]["product"]["variations"]] == ["OHI01-TRANG"]


def test_nhom_nam_o_nhieu_san_pham_pancake_la_xung_dot_ca_nhom(ohi):
    pancake = FakePancake()
    pancake.seed("X", [{"id": "v1", "custom_id": "OHI01-DEN"}])
    pancake.seed("Y", [{"id": "v2", "custom_id": "OHI01-TRANG"}])
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.conflicts == 2
    assert pancake.write_calls() == []
    assert set(PancakePosVariationLink.objects.values_list("status", flat=True)) == {LinkStatus.CONFLICT}


def test_loi_mot_nhom_khong_chan_nhom_khac(ohi):
    goi = _catalog("GOI01", "Gối massage cổ")
    pancake = FakePancake()
    pancake.fail_on["OHI01"] = "Tên sản phẩm không hợp lệ"
    report = _service(pancake, [*ohi, goi]).full_sync(dry_run=False)
    assert report.counters.errors == 2 and report.counters.created == 1
    assert PancakePosVariationLink.objects.get(product_id=goi.id).status == LinkStatus.SYNCED
    failed = PancakePosVariationLink.objects.get(product_id=ohi[0].id)
    assert failed.status == LinkStatus.ERROR and failed.attempts == 1
    assert "Tên sản phẩm không hợp lệ" in failed.last_error

    # Lượt sau vẫn lỗi → attempts tăng; hết lỗi → synced, attempts về 0
    _service(pancake, [*ohi, goi]).full_sync(dry_run=False)
    assert PancakePosVariationLink.objects.get(product_id=ohi[0].id).attempts == 2
    del pancake.fail_on["OHI01"]
    _service(pancake, [*ohi, goi]).full_sync(dry_run=False)
    fixed = PancakePosVariationLink.objects.get(product_id=ohi[0].id)
    assert fixed.status == LinkStatus.SYNCED and fixed.attempts == 0 and fixed.last_error == ""


def test_post_xong_khong_doc_lai_duoc_thi_loi_va_luot_sau_lien_ket(ohi):
    pancake = FakePancake()
    pancake.hide_after_create = True
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.errors == 2
    assert "không đọc lại" in report.errors[0]["message"]
    # Pancake "hiện" sản phẩm ra → lượt sau liên kết, không POST lần nữa
    for product in pancake.products.values():
        product["hidden"] = False
    pancake.calls.clear()
    report = _service(pancake, ohi).full_sync(dry_run=False)
    assert report.counters.linked == 2 and report.counters.created == 0
    assert [c[0] for c in pancake.write_calls()] == ["update"]
    assert len(pancake.products) == 1


def test_dry_run_khong_goi_api_ghi_va_khong_ghi_lien_ket(ohi):
    goi = _catalog("GOI01", "Gối massage cổ")
    old = _catalog("OLD-2", "Hàng cũ", has_stock=False)
    pancake = FakePancake()
    pancake.seed("GOI01", [{"id": "vg", "custom_id": "GOI01"}])
    report = _service(pancake, [*ohi, goi, old]).full_sync(dry_run=True)
    assert pancake.write_calls() == []
    assert not PancakePosVariationLink.objects.exists()
    assert not PancakePosProductLink.objects.exists()
    assert report.counters.created == 2 and report.counters.linked == 1
    assert report.ineligible == {"sku_legacy_suffix": 1}
    assert len(report.samples) == 2
    assert {s["action"] for s in report.samples} == {"create", "update"}
    run = PancakePosSyncRun.objects.get(id=report.run_id)
    assert run.mode == "dry_run" and run.created == 2 and run.report["samples"]


def test_limit_theo_nhom_va_so_mau_toi_da():
    products = [_catalog(f"SKU{i:03d}", f"Sản phẩm {i}") for i in range(25)]
    pancake = FakePancake()
    report = _service(pancake, products).full_sync(dry_run=True)
    assert len(report.samples) == 20
    report = _service(pancake, products).full_sync(dry_run=True, limit=3)
    assert report.groups == 3 and report.counters.created == 3


def test_loi_toan_luot_van_dong_sync_run(ohi):
    class Broken(FakePancake):
        def iter_variations(self, *, search=None):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        _service(Broken(), ohi).full_sync(dry_run=False)
    run = PancakePosSyncRun.objects.get()
    assert run.finished_at is not None and run.report["fatal"] == "RuntimeError"
