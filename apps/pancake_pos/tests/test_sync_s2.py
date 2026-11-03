"""S2: PUT luôn gửi đủ mẫu mã (§7), ẩn SKU hết đủ điều kiện, đồng bộ tăng dần.

FakePancake mặc định mô phỏng trường hợp XẤU NHẤT: PUT thiếu mẫu mã nào thì xoá
mẫu mã đó — mọi test ở đây đều phải giữ nguyên mẫu mã không thuộc CRM.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.pancake_pos.application.services.product_sync_service import ProductSyncService
from apps.pancake_pos.domain.entities import SYNC_MODE_ALWAYS, SYNC_MODE_NEVER, CatalogProduct
from apps.pancake_pos.models import LinkStatus, PancakePosSyncRun, PancakePosVariationLink
from apps.pancake_pos.tests.fakes import FakePancake, FakeReader

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
FOREIGN = {"id": "vLA", "custom_id": "HANG-SAN", "barcode": "HANG-SAN", "retail_price": 77000,
           "fields": [{"name": "Mẫu", "value": "Hàng sàn"}], "is_hidden": False}


def _catalog(sku: str, name: str, group: str | None = None, **kw) -> CatalogProduct:
    product = Product.objects.create(sku=sku, name=name, price=Decimal(kw.pop("price", 100000)))
    kw.setdefault("has_stock", True)
    return CatalogProduct(id=product.id, sku=sku, name=name, group_code=group, price=product.price, **kw)


def _service(pancake: FakePancake, reader: FakeReader) -> ProductSyncService:
    return ProductSyncService(gateway=pancake, catalog_reader=reader, clock=lambda: NOW)


@pytest.fixture
def ohi():
    return [
        _catalog("OHI01-DEN", "Ghế massage toàn thân - Đen", "OHI01"),
        _catalog("OHI01-TRANG", "Ghế massage toàn thân - Trắng", "OHI01"),
    ]


def _link(product: CatalogProduct) -> PancakePosVariationLink:
    return PancakePosVariationLink.objects.get(product_id=product.id)


def _put_bodies(pancake: FakePancake) -> list[dict]:
    return [c[1][1]["product"] for c in pancake.calls if c[0] == "update"]


# ── 0. PUT không bao giờ thiếu mẫu mã đang có ───────────────────────────────
def test_put_gui_kem_mau_ma_la_nguyen_trang(ohi):
    pancake = FakePancake()
    pid = pancake.seed("OHI01", [
        {"id": "v1", "custom_id": "OHI01-DEN", "barcode": "OHI01-DEN"},
        FOREIGN,
    ])
    report = _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)

    assert report.counters.linked == 1 and report.counters.created == 1
    body = _put_bodies(pancake)[0]
    foreign = next(v for v in body["variations"] if v.get("id") == "vLA")
    assert foreign == {**FOREIGN}  # id + trường đọc về, không đổi gì
    # Mẫu mã lạ vẫn còn trên Pancake, giá không bị đụng
    assert pancake.variation("vLA")["retail_price"] == 77000
    assert {v["id"] for v in pancake.products[pid]["variations"]} >= {"v1", "vLA"}
    # Thuộc tính của mẫu mã lạ được gộp vào product_attributes
    assert "Hàng sàn" in body["product_attributes"][0]["values"]
    # GET sản phẩm ngay trước PUT để biết đủ mẫu mã hiện có
    assert ("get", pid) in pancake.calls


def test_mau_ma_la_xuat_hien_sau_khi_keo_chi_muc_van_duoc_giu(ohi):
    """Chỉ mục kéo đầu lượt có thể cũ — GET product trước PUT là nguồn chính."""
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    pid = next(iter(pancake.products))
    pancake.products[pid]["variations"].append(dict(FOREIGN))  # ai đó thêm tay trên Pancake

    changed = [replace(ohi[0], price=Decimal(90000)), ohi[1]]
    _service(pancake, FakeReader(changed)).full_sync(dry_run=False)
    assert any(v["id"] == "vLA" for v in pancake.products[pid]["variations"])


def test_khong_doc_duoc_san_pham_truoc_put_thi_khong_put(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    pid = next(iter(pancake.products))
    pancake.calls.clear()
    pancake.get_product = lambda ref: None  # type: ignore[method-assign]

    changed = [replace(ohi[0], price=Decimal(90000)), ohi[1]]
    report = _service(pancake, FakeReader(changed)).full_sync(dry_run=False)
    assert report.counters.errors == 2
    assert pancake.write_calls() == []
    assert pid in report.errors[0]["message"]


def test_dry_run_mau_payload_co_mau_ma_la(ohi):
    pancake = FakePancake()
    pancake.seed("OHI01", [{"id": "v1", "custom_id": "OHI01-DEN"}, FOREIGN])
    report = _service(pancake, FakeReader(ohi)).full_sync(dry_run=True)
    ids = [v.get("id") for v in report.samples[0]["request"]["product"]["variations"]]
    assert "vLA" in ids
    assert pancake.write_calls() == []
    assert ("get", "p1") not in pancake.calls  # dry-run chỉ dùng chỉ mục, không GET thêm


# ── 1. ẩn thay vì xoá ──────────────────────────────────────────────────────
def test_sku_het_du_dieu_kien_trong_nhom_thi_an_cung_lan_put(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    den_vid = _link(ohi[0]).pancake_variation_id

    never = [replace(ohi[0], sync_mode=SYNC_MODE_NEVER), ohi[1]]
    report = _service(pancake, FakeReader(never)).full_sync(dry_run=False)

    assert report.counters.hidden == 1
    assert pancake.variation(den_vid)["is_hidden"] is True  # còn trên Pancake, chỉ ẩn
    link = _link(ohi[0])
    assert link.status == LinkStatus.SKIPPED and "mode_never" in link.last_error
    assert link.pancake_variation_id == den_vid
    assert PancakePosSyncRun.objects.get(id=report.run_id).hidden == 1

    pancake.calls.clear()
    again = _service(pancake, FakeReader(never)).full_sync(dry_run=False)
    assert again.counters.hidden == 0 and pancake.write_calls() == []


def test_ca_nhom_het_du_dieu_kien_thi_put_chi_an(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    pid = next(iter(pancake.products))
    pancake.products[pid]["variations"].append(dict(FOREIGN))

    # Cả nhóm bị xoá mềm → reader không trả về dòng nào.
    report = _service(pancake, FakeReader([])).full_sync(dry_run=False)

    assert report.counters.hidden == 2
    variations = {v["id"]: v for v in pancake.products[pid]["variations"]}
    assert "vLA" in variations and variations["vLA"].get("is_hidden") is False
    assert all(v["is_hidden"] for k, v in variations.items() if k != "vLA")
    assert {_link(p).status for p in ohi} == {LinkStatus.SKIPPED}
    assert all("deleted" in _link(p).last_error for p in ohi)
    assert pancake.products[pid]["custom_id"] == "OHI01"


def test_limit_khong_an_hang_loat(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    pancake.calls.clear()
    report = _service(pancake, FakeReader([])).full_sync(dry_run=False, limit=5)
    assert report.counters.hidden == 0 and pancake.write_calls() == []


def test_du_dieu_kien_lai_thi_hien_lai(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    never = [replace(ohi[0], sync_mode=SYNC_MODE_NEVER), ohi[1]]
    _service(pancake, FakeReader(never)).full_sync(dry_run=False)
    den_vid = _link(ohi[0]).pancake_variation_id

    report = _service(pancake, FakeReader([replace(ohi[0], sync_mode=SYNC_MODE_ALWAYS), ohi[1]])).full_sync(dry_run=False)
    assert report.counters.updated == 2
    assert pancake.variation(den_vid)["is_hidden"] is False
    assert _link(ohi[0]).status == LinkStatus.SYNCED


def test_dry_run_dem_so_se_an_khong_ghi(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    pancake.calls.clear()
    report = _service(pancake, FakeReader([])).full_sync(dry_run=True)
    assert report.counters.hidden == 2
    assert report.samples[0]["action"] == "hide"
    assert pancake.write_calls() == []
    assert {_link(p).status for p in ohi} == {LinkStatus.SYNCED}


# ── 2. đồng bộ tăng dần ────────────────────────────────────────────────────
def test_tang_dan_nhom_khong_doi_khong_goi_api(ohi):
    pancake = FakePancake()
    reader = FakeReader(ohi)
    _service(pancake, reader).full_sync(dry_run=False)
    pancake.calls.clear()

    report = _service(pancake, reader).sync_products([ohi[0].id])
    assert report.mode == "incremental"
    assert report.counters.skipped == 2
    assert pancake.calls == []
    assert reader.calls[-1]["product_ids"] == [ohi[0].id]
    assert PancakePosSyncRun.objects.get(id=report.run_id).mode == "incremental"


def test_tang_dan_doi_gia_chi_put_nhom_do(ohi):
    goi = _catalog("GOI01", "Gối massage cổ")
    pancake = FakePancake()
    _service(pancake, FakeReader([*ohi, goi])).full_sync(dry_run=False)
    pid = next(p for p, v in pancake.products.items() if v["custom_id"] == "OHI01")
    pancake.products[pid]["variations"].append(dict(FOREIGN))
    pancake.calls.clear()

    changed = [replace(ohi[0], price=Decimal(95000)), ohi[1], goi]
    report = _service(pancake, FakeReader(changed)).sync_products([ohi[0].id])
    assert report.counters.updated == 2
    updates = [c for c in pancake.write_calls() if c[0] == "update"]
    assert [c[1][0] for c in updates] == [pid]
    assert any(v["id"] == "vLA" for v in pancake.products[pid]["variations"])
    assert not any(c[0] == "iter" and c[1] is None for c in pancake.calls)  # không kéo toàn shop


def test_tang_dan_sku_moi_them_vao_san_pham_nhom(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    xam = _catalog("OHI01-XAM", "Ghế massage toàn thân - Xám", "OHI01")
    pancake.calls.clear()

    report = _service(pancake, FakeReader([*ohi, xam])).sync_products([xam.id])
    assert report.counters.created == 1
    assert ("iter", "OHI01-XAM") in pancake.calls  # tìm theo SKU trước khi tạo
    assert _link(xam).status == LinkStatus.SYNCED
    assert len(pancake.products) == 1


def test_tang_dan_lien_ket_hang_co_san_theo_custom_id_nhom():
    moi = [_catalog("MDD01-XAM", "Máy mát xa MDD01 - Xám", "MDD01")]
    pancake = FakePancake()
    pid = pancake.seed("MDD01", [{"id": "vz", "custom_id": "KHAC", "barcode": ""}])
    report = _service(pancake, FakeReader(moi)).sync_products([moi[0].id])
    assert report.counters.created == 1
    assert ("get", "MDD01") in pancake.calls
    assert len(pancake.products) == 1
    assert {v["id"] for v in pancake.products[pid]["variations"]} >= {"vz"}


def test_tang_dan_san_pham_xoa_mem_thi_an(ohi):
    pancake = FakePancake()
    _service(pancake, FakeReader(ohi)).full_sync(dry_run=False)
    den_vid = _link(ohi[0]).pancake_variation_id
    Product.objects.filter(id=ohi[0].id).update(deleted_at=NOW)

    # Reader không trả SP đã xoá mềm nhưng vẫn trả anh em cùng nhóm.
    reader = FakeReader([ohi[1]])
    reader.deleted_groups = {ohi[0].id: "OHI01"}
    report = _service(pancake, reader).sync_products([ohi[0].id])
    assert report.counters.hidden == 1
    assert pancake.variation(den_vid)["is_hidden"] is True
    assert _link(ohi[0]).status == LinkStatus.SKIPPED


def test_tang_dan_rong_khong_goi_gi():
    pancake = FakePancake()
    reader = FakeReader([])
    report = _service(pancake, reader).sync_products([])
    assert report.requested == 0 and reader.calls == [] and pancake.calls == []
