"""Domain thuần: luật chọn, ghép khuôn, hash, chỉ mục Pancake — không cần DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.pancake_pos.domain import eligibility as el
from apps.pancake_pos.domain.entities import CatalogProduct, PancakeVariation, SyncGroup
from apps.pancake_pos.domain.hashing import stable_hash
from apps.pancake_pos.domain.mapping import (
    ATTRIBUTE_NAME,
    build_group_payload,
    common_word_prefix,
    group_products,
    merge_attributes,
    passthrough_variation,
    product_display_name,
    to_int_vnd,
    variation_labels,
    weight_grams,
)
from apps.pancake_pos.domain.pancake_index import build_index

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _p(pid: int = 1, sku: str = "OHI01-DEN", name: str = "Ghế massage OHI01 Đen", **kw) -> CatalogProduct:
    kw.setdefault("has_stock", True)
    return CatalogProduct(id=pid, sku=sku, name=name, **kw)


# ── eligibility ─────────────────────────────────────────────────────────────
class TestEligibility:
    def test_con_ton_la_du_dieu_kien(self):
        assert el.is_syncable(_p(), now=NOW)

    def test_ban_trong_cua_so_180_ngay(self):
        p = _p(has_stock=False, last_sold_at=NOW - timedelta(days=180))
        assert el.is_syncable(p, now=NOW)
        old = _p(has_stock=False, last_sold_at=NOW - timedelta(days=181))
        assert el.ineligible_reason(old, now=NOW) == el.REASON_NO_ACTIVITY
        # Ngưỡng là tham số
        assert el.is_syncable(old, now=NOW, sales_window_days=200)

    def test_khong_ton_khong_ban(self):
        assert el.ineligible_reason(_p(has_stock=False), now=NOW) == el.REASON_NO_ACTIVITY

    def test_loai_theo_sku(self):
        assert el.ineligible_reason(_p(sku="OHI01{DEL}"), now=NOW) == el.REASON_DELETED_MARKER
        assert el.ineligible_reason(_p(sku="ohi01{del}"), now=NOW) == el.REASON_DELETED_MARKER
        assert el.ineligible_reason(_p(sku="OHI01-2"), now=NOW) == el.REASON_LEGACY_SUFFIX
        assert el.ineligible_reason(_p(sku="OHI01+GOI01"), now=NOW) == el.REASON_COMBO
        assert el.ineligible_reason(_p(is_combo=True), now=NOW) == el.REASON_COMBO
        assert el.ineligible_reason(_p(is_active=False), now=NOW) == el.REASON_INACTIVE
        assert el.ineligible_reason(_p(sku="  "), now=NOW) == el.REASON_EMPTY_SKU
        # -N chỉ ở CUỐI mới là lô cũ
        assert el.is_syncable(_p(sku="OHI-01A"), now=NOW)

    def test_sync_mode_ghi_de(self):
        assert el.is_syncable(_p(sku="OHI01-2", has_stock=False, is_active=False, sync_mode="always"), now=NOW)
        assert el.ineligible_reason(_p(sync_mode="never"), now=NOW) == el.REASON_NEVER
        # always không cứu được SKU rỗng
        assert not el.is_syncable(_p(sku="", sync_mode="always"), now=NOW)


# ── mapping ─────────────────────────────────────────────────────────────────
class TestCommonPrefix:
    def test_tieng_viet_co_dau_va_hoa_thuong(self):
        names = ["Ghế massage toàn thân OHI01 - Đen", "ghế Massage toàn thân OHI01 - Trắng"]
        assert common_word_prefix(names) == "Ghế massage toàn thân OHI01"

    def test_theo_tu_khong_cat_giua_tu(self):
        assert common_word_prefix(["Máy lọc nước", "Máy lọc nóng"]) == "Máy lọc"

    def test_nfc_nfd_cung_mot_tu(self):
        import unicodedata

        nfd = unicodedata.normalize("NFD", "Gối Đỏ")
        assert common_word_prefix(["Gối Đỏ", nfd]) == "Gối Đỏ"

    def test_khong_chung_gi(self):
        assert common_word_prefix(["Ghế", "Bàn"]) == ""
        assert common_word_prefix([]) == ""


def _group(products, key="OHI01", grouped=True) -> SyncGroup:
    return SyncGroup(key=key, custom_id=key, products=tuple(products), is_grouped=grouped)


class TestMapping:
    def test_ten_va_nhan_mau_ma_nhom(self):
        g = _group([
            _p(1, "OHI01-DEN", "Ghế massage toàn thân OHI01 - Đen"),
            _p(2, "OHI01-TRANG", "Ghế massage toàn thân OHI01 - Trắng"),
        ])
        assert product_display_name(g) == "OHI01 - Ghế massage toàn thân OHI01"
        assert variation_labels(g) == ["Đen", "Trắng"]

    def test_tien_to_ngan_thi_lay_ten_sku_dau(self):
        g = _group([_p(1, "MDD01-A", "Mũ A"), _p(2, "MDD01-B", "Mũ B")], key="MDD01")
        assert product_display_name(g) == "Mũ A"

    def test_nhom_1_sku_nhan_lay_tu_sku(self):
        g = _group([_p(1, "OHI01-DEN", "Ghế massage Đen")])
        assert product_display_name(g) == "OHI01 - Ghế massage Đen"
        assert variation_labels(g) == ["DEN"]

    def test_nhan_trung_duoc_phan_biet(self):
        g = _group([_p(1, "OHI01-A", "Ghế Đen"), _p(2, "OHI01-B", "Ghế đen")])
        labels = variation_labels(g)
        assert len({x.casefold() for x in labels}) == 2

    def test_payload_nhom(self):
        g = _group([
            _p(1, "OHI01-DEN", "Ghế massage toàn thân - Đen", price=Decimal("12490000.50"), weight=Decimal("0"),
               length=Decimal("120"), width=Decimal("0"), height=Decimal("0")),
            _p(2, "OHI01-TRANG", "Ghế massage toàn thân - Trắng", price=Decimal("12000000"), weight=Decimal("35000")),
        ])
        payload = build_group_payload(g, default_weight_g=500)
        req = payload.to_request({1: "var-1"})["product"]
        assert req["name"] == "OHI01 - Ghế massage toàn thân"
        assert req["custom_id"] == "OHI01"
        assert req["weight"] == 500
        assert req["product_attributes"] == [{"name": ATTRIBUTE_NAME, "values": ["Đen", "Trắng"]}]
        v1, v2 = req["variations"]
        assert v1 == {
            "id": "var-1", "custom_id": "OHI01-DEN", "barcode": "OHI01-DEN", "retail_price": 12490001,
            "weight": 500, "fields": [{"name": ATTRIBUTE_NAME, "value": "Đen"}],
            "size": {"length": 120, "width": 0, "height": 0}, "is_hidden": False,
        }
        assert "id" not in v2 and "size" not in v2
        assert v2["weight"] == 35000 and v2["retail_price"] == 12000000

    def test_sku_khong_nhom(self):
        g = _group([_p(7, "GOI01", "Gối massage cổ")], key="sku:GOI01", grouped=False)
        g = SyncGroup(key="sku:GOI01", custom_id="GOI01", products=g.products, is_grouped=False)
        req = build_group_payload(g, default_weight_g=500).to_request()["product"]
        assert req["name"] == "Gối massage cổ"
        assert req["custom_id"] == "GOI01"
        assert "product_attributes" not in req
        assert "fields" not in req["variations"][0]

    def test_hash_khong_doi_khi_gan_id(self):
        g = _group([_p(1), _p(2, "OHI01-TRANG", "Ghế massage OHI01 Trắng")])
        a = build_group_payload(g, default_weight_g=500)
        b = build_group_payload(g, default_weight_g=500)
        assert a.hash == b.hash and len(a.hash) == 64
        c = build_group_payload(_group([_p(1, price=Decimal(1)), _p(2, "OHI01-TRANG", "Ghế massage OHI01 Trắng")]),
                                default_weight_g=500)
        assert c.hash != a.hash

    def test_so_nguyen(self):
        assert to_int_vnd(Decimal("99.5")) == 100
        assert to_int_vnd(None) == 0
        assert to_int_vnd(Decimal("-5")) == 0
        assert weight_grams(Decimal("0.4"), 500) == 500
        assert weight_grams(Decimal("250.6"), 500) == 251


class TestShortName:
    """Tên ngắn (products.short_name) thay `name` khi đặt tên trên Pancake."""

    def test_display_name_uu_tien_ten_ngan(self):
        assert _p(name="Tên hoá đơn rất dài", short_name="  Ghế OHI01  ").display_name == "Ghế OHI01"
        assert _p(name=" Ghế dài ", short_name="   ").display_name == "Ghế dài"

    def test_nhom_co_ten_ngan(self):
        g = _group([
            _p(1, "OHI01-DEN", "GHẾ MASSAGE TOÀN THÂN CAO CẤP OHI01 MÀU ĐEN (HĐ)", short_name="Ghế OHI01 Đen"),
            _p(2, "OHI01-TRANG", "GHẾ MASSAGE TOÀN THÂN CAO CẤP OHI01 MÀU TRẮNG", short_name="Ghế OHI01 Trắng"),
        ])
        assert product_display_name(g) == "OHI01 - Ghế OHI01"
        assert variation_labels(g) == ["Đen", "Trắng"]
        req = build_group_payload(g, default_weight_g=500).to_request()["product"]
        assert req["name"] == "OHI01 - Ghế OHI01"
        assert [v["fields"][0]["value"] for v in req["variations"]] == ["Đen", "Trắng"]

    def test_sku_le_co_ten_ngan(self):
        g = SyncGroup(key="sku:GOI01", custom_id="GOI01", is_grouped=False,
                      products=(_p(7, "GOI01", "GỐI MASSAGE CỔ HỒNG NGOẠI PHIÊN BẢN 2026", short_name="Gối cổ"),))
        assert build_group_payload(g, default_weight_g=500).to_request()["product"]["name"] == "Gối cổ"

    def test_tron_co_va_khong_co_ten_ngan(self):
        g = _group([
            _p(1, "OHI01-DEN", "Ghế massage toàn thân OHI01 - Đen", short_name="Ghế massage toàn thân OHI01 - Đen nhám"),
            _p(2, "OHI01-TRANG", "Ghế massage toàn thân OHI01 - Trắng"),
        ])
        # SKU không có tên ngắn dùng `name`; tiền tố chung tính trên tên hiển thị.
        assert product_display_name(g) == "OHI01 - Ghế massage toàn thân OHI01"
        assert variation_labels(g) == ["Đen nhám", "Trắng"]

    def test_hash_doi_khi_ten_ngan_doi(self):
        base = [_p(1), _p(2, "OHI01-TRANG", "Ghế massage OHI01 Trắng")]
        a = build_group_payload(_group(base), default_weight_g=500)
        b = build_group_payload(_group([_p(1, short_name="Ghế OHI01 Đen"), base[1]]), default_weight_g=500)
        assert a.hash != b.hash
        assert a.variations[0].hash != b.variations[0].hash  # nhãn "Mẫu" đổi


class TestGrouping:
    def test_gom_nhom_va_sku_le(self):
        products = [
            _p(3, "OHI01-TRANG", group_code="OHI01"),
            _p(1, "OHI01-DEN", group_code="OHI01"),
            _p(2, "GOI01", group_code=None),
            _p(4, "MDD01-X", group_code=" MDD01 "),
        ]
        groups = group_products(products)
        assert [g.key for g in groups] == ["MDD01", "OHI01", "sku:GOI01"]
        ohi = groups[1]
        assert [p.sku for p in ohi.products] == ["OHI01-DEN", "OHI01-TRANG"]
        assert ohi.is_grouped and not groups[2].is_grouped
        assert groups[2].custom_id == "GOI01"


# ── hashing ─────────────────────────────────────────────────────────────────
def test_stable_hash_khong_phu_thuoc_thu_tu_khoa():
    assert stable_hash({"a": 1, "b": "Đen"}) == stable_hash({"b": "Đen", "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


# ── chỉ mục Pancake ────────────────────────────────────────────────────────
def test_index_theo_custom_id_va_barcode():
    index = build_index([
        PancakeVariation(id="v1", product_id="p1", custom_id="ohi01-den", barcode="OHI01-DEN", product_custom_id="OHI01"),
        PancakeVariation(id="v2", product_id="p2", custom_id="", barcode="OHI01-DEN"),
        PancakeVariation(id="v3", product_id="p3", custom_id="GOI01"),
        PancakeVariation(id="", product_id="p4", custom_id="BO-QUA"),
    ])
    assert [v.id for v in index.lookup(" OHI01-den ")] == ["v1", "v2"]  # trùng → xung đột
    assert [v.id for v in index.lookup("GOI01")] == ["v3"]
    assert index.lookup("BO-QUA") == []
    assert index.product_ids_for("ohi01") == {"p1"}
    assert index.product_id_of("v3") == "p3"
    assert index.total_variations == 3

    assert [v.id for v in index.variations_of("p1")] == ["v1"]
    assert index.complete is True
    assert build_index([], complete=False).complete is False


# ── PUT gửi đủ mẫu mã (§7) ─────────────────────────────────────────────────
def test_passthrough_giu_nguyen_trang_va_an_khi_can():
    la = PancakeVariation(
        id="vX", product_id="p1", custom_id="LA-01",
        raw={"custom_id": "LA-01", "retail_price": 150000, "fields": [{"name": "Màu", "value": "Đỏ"}]},
    )
    assert passthrough_variation(la) == {
        "id": "vX", "custom_id": "LA-01", "retail_price": 150000, "fields": [{"name": "Màu", "value": "Đỏ"}],
    }
    assert passthrough_variation(la, hide=True)["is_hidden"] is True
    assert "is_hidden" not in la.raw  # không sửa dữ liệu gốc


def test_to_request_kem_mau_ma_la_va_gop_thuoc_tinh():
    g = SyncGroup(key="OHI01", custom_id="OHI01", is_grouped=True, products=(
        _p(1, "OHI01-DEN", "Ghế OHI01 massage - Đen"), _p(2, "OHI01-TRANG", "Ghế OHI01 massage - Trắng"),
    ))
    payload = build_group_payload(g, default_weight_g=500)
    extra = {"id": "vX", "fields": [{"name": ATTRIBUTE_NAME, "value": "Xám"}, {"name": "Size", "value": "L"}]}
    req = payload.to_request({1: "v1", 2: "v2"}, [extra])["product"]
    assert [v.get("id") for v in req["variations"]] == ["v1", "v2", "vX"]
    assert req["product_attributes"] == [
        {"name": ATTRIBUTE_NAME, "values": ["Đen", "Trắng", "Xám"]}, {"name": "Size", "values": ["L"]},
    ]
    # hash chỉ theo nội dung CRM — mẫu mã lạ không làm đổi hash
    assert payload.hash == build_group_payload(g, default_weight_g=500).hash


def test_merge_attributes_khong_trung():
    merged = merge_attributes([{"name": "Mẫu", "values": ["A"]}], [{"fields": [{"name": "Mẫu", "value": "A"}]}])
    assert merged == [{"name": "Mẫu", "values": ["A"]}]
