"""Luật thuần webhook Pancake → CRM (two-way-sync B1): phân loại payload, snapshot đơn,
mã tra cứu, mốc gửi/giao/hoàn, trạng thái vận chuyển trong snapshot, dựng đơn."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.pancake_pos.domain import inbound_order as rules
from apps.pancake_pos.domain.inbound_order import ResolvedProduct


def order_payload(**kw) -> dict:
    """Đơn Shopee theo hình dạng thật (GET orders 30/09/2026), đã bỏ PII."""
    base = {
        "id": "260930BY9FBX6H", "status": 2, "status_name": "shipped", "order_sources": "-3",
        "order_sources_name": "Shopee", "marketplace_id": "-3", "account": "922017502",
        "account_name": "DELAX MASSAGER VN", "page_id": "spo_512453061",
        "updated_at": "2026-09-29T20:06:41.410614", "inserted_at": "2026-09-29T20:06:01.000000",
        "bill_full_name": "Khách A", "bill_phone_number": "******", "custom_id": None,
        "total_price": 3000000, "total_discount": 0, "total_price_after_sub_discount": 1576185,
        "partner": {"extend_code": "SPXVN0001", "partner_name": "Shopee Xpress"},
        "shipping_address": {"full_address": "1 Trần Phú, Hà Đông, Hà Nội"},
        "items": [{"id": 1, "variation_id": None, "quantity": 1, "discount_each_product": 1001000,
                   "variation_info": {"display_id": "DLX-01", "name": "Máy massage", "retail_price": 3000000}}],
        "status_history": [
            {"status": 0, "updated_at": "2026-09-29T20:06:01"},
            {"status": 2, "updated_at": "2026-09-29T22:00:00"},
        ],
    }
    base.update(kw)
    return base


def test_phan_loai_don_ton_san_pham_khach_va_la():
    ev = rules.classify_payload(order_payload())
    assert (ev.event_type, ev.record_id) == ("orders", "260930BY9FBX6H")
    assert ev.version == datetime(2026, 9, 29, 20, 6, 41, 410614, tzinfo=UTC)
    wrapped = rules.classify_payload({"data": order_payload(id="X1")})
    assert (wrapped.event_type, wrapped.record_id) == ("orders", "X1")
    inv = rules.classify_payload({"data": {"record": {
        "variation_id": "v1", "warehouse_id": "w1", "change_quantity": -1, "inserted_at": "2026-09-30T01:00:00",
    }}})
    assert (inv.event_type, inv.record_id) == ("variations_warehouses", "v1:w1")
    prod = rules.classify_payload({"data": {"record": {"id": "p1", "name": "SP", "variations": []}}})
    assert (prod.event_type, prod.record_id) == ("products", "p1")
    assert rules.classify_payload({"id": "c1", "phone_numbers": ["x"]}).event_type == "customers"
    assert rules.classify_payload({"foo": 1}).event_type == "unknown"
    assert rules.classify_payload([1, 2]).event_type == "unknown"


def test_hash_on_dinh_theo_noi_dung():
    assert rules.payload_hash({"a": 1, "b": 2}) == rules.payload_hash({"b": 2, "a": 1})
    assert rules.payload_hash({"a": 1}) != rules.payload_hash({"a": 2})


def test_snapshot_va_ma_tra_cuu():
    snap = rules.parse_order_snapshot(order_payload())
    assert snap is not None and snap.status == 2 and snap.tracking_code == "SPXVN0001"
    assert rules.marketplace_codes(snap) == ("HDSPE_260930BY9FBX6H", "DHSPE_260930BY9FBX6H")
    tiktok = rules.parse_order_snapshot(order_payload(id="586324092416198091", order_sources="-9", marketplace_id="-9"))
    assert rules.marketplace_codes(tiktok) == ("HDTTS_586324092416198091", "DHTTS_586324092416198091")
    page = rules.parse_order_snapshot(order_payload(order_sources="-1", marketplace_id=None))
    assert rules.marketplace_codes(page) == ()
    assert rules.parse_order_snapshot({"status": 1}) is None
    assert rules.crm_ref("123") == (123, 1)
    assert rules.crm_ref("123-2") == (123, 2)
    assert rules.crm_ref("CRM-123") == (123, 1)  # định dạng cũ
    assert rules.crm_ref("CRM-123-2") == (123, 2)
    assert rules.crm_ref("260929BGBCJTAY") is None  # mã đơn sàn
    assert rules.crm_ref("586336200996258932") is None  # mã đơn TikTok (18 số)
    assert rules.crm_ref("") is None and rules.crm_ref("abc") is None


def test_thu_tu_phien_ban():
    older, newer = datetime(2026, 9, 29, 1, tzinfo=UTC), datetime(2026, 9, 29, 2, tzinfo=UTC)
    assert rules.is_stale(older, newer)
    assert not rules.is_stale(newer, older)
    assert not rules.is_stale(newer, newer)  # bằng nhau vẫn áp (idempotent)
    assert not rules.is_stale(None, newer) and not rules.is_stale(older, None)


def test_moc_gui_giao_hoan():
    snap = rules.parse_order_snapshot(order_payload(status=3, status_history=[
        {"status": 2, "updated_at": "2026-09-29T22:00:00"},
        {"status": 3, "updated_at": "2026-10-01T08:00:00"},
    ]))
    marks = rules.milestones(snap)
    assert marks.shipped_at == datetime(2026, 9, 29, 22, tzinfo=UTC)
    assert marks.delivered_at == datetime(2026, 10, 1, 8, tzinfo=UTC)
    assert marks.returned_at is None
    # Thiếu lịch sử: suy từ trạng thái hiện tại.
    bare = rules.parse_order_snapshot(order_payload(status=5, status_history=[], time_send_partner="2026-09-29T10:00:00"))
    marks = rules.milestones(bare)
    assert marks.shipped_at == datetime(2026, 9, 29, 10, tzinfo=UTC)
    assert marks.returned_at == bare.updated_at


def test_snapshot_trang_thai_van_chuyen_va_kho():
    snap = rules.parse_order_snapshot(order_payload(
        status=2, warehouse_id="wh-1", warehouse_info={"name": "Đại Mỗ 1"},
        partner={"extend_code": "SPX1", "partner_name": "Shopee Xpress", "partner_status": "on_the_way",
                 "updated_at": "2026-09-30T03:00:00"},
    ))
    assert (snap.status_name, snap.warehouse_id, snap.warehouse_name) == ("shipped", "wh-1", "Đại Mỗ 1")
    assert snap.dvvc.label == "Đang trung chuyển"
    assert snap.dvvc.at == datetime(2026, 9, 30, 3, tzinfo=UTC)
    # Lúc vào trạng thái hiện tại = mục lịch sử mới nhất có đúng trạng thái đó.
    assert snap.status_changed_at == datetime(2026, 9, 29, 22, tzinfo=UTC)
    no_hist = rules.parse_order_snapshot(order_payload(status=3, status_history=[]))
    assert no_hist.status_changed_at == no_hist.updated_at


def test_ma_dvvc_va_cau_hinh():
    assert rules.provider_code("Giao hàng nhanh") == "GHN"
    assert rules.provider_code("Shopee Xpress") == "SPX"
    assert rules.provider_code("Best Inc") == "BEST"
    assert rules.provider_code("Ahamove") == "Ahamove"
    assert rules.parse_source_map("-3=shopee, -9=TikTok,bad,=x") == {"-3": "shopee", "-9": "tiktok"}
    assert rules.parse_channel_map("922017502=34,spo_1=x,tts_2=40") == {"922017502": 34, "tts_2": 40}


def test_dung_don_crm_gop_dong_va_thieu_san_pham():
    payload = order_payload(items=[
        {"variation_id": "v1", "quantity": 1, "discount_each_product": 100,
         "variation_info": {"display_id": "A", "name": "SP A", "retail_price": 1000}},
        {"variation_id": "v1", "quantity": 2, "discount_each_product": 100,
         "variation_info": {"display_id": "A", "name": "SP A", "retail_price": 1000}},
        {"variation_id": None, "quantity": 1, "discount_each_product": 5000,
         "variation_info": {"display_id": "b-2", "name": "SP B", "retail_price": 2000}},
        {"variation_id": None, "quantity": 1, "variation_info": {"display_id": "Z", "name": "Lạ", "retail_price": 1}},
    ], total_price=None, total_discount=0, total_price_after_sub_discount=None)
    snap = rules.parse_order_snapshot(payload)
    products = {"var:v1": ResolvedProduct(10, "A", "SP A"), "sku:B-2": ResolvedProduct(11, "B-2", "SP B")}
    new = rules.build_new_order(snap, channel_id=34, order_source="shopee", status=2, products=products)
    assert [(line.product_id, line.quantity, line.discount) for line in new.lines] == [(10, 3, 100), (11, 1, 2000)]
    assert new.missing_items == ("Z Lạ",)
    assert new.total_amount == 1000 * 3 + 2000 + 1
    assert new.discount_amount == 100 * 3 + 2000  # giảm mỗi SP không vượt giá
    assert new.total_after_discount == new.total_amount - new.discount_amount
    assert (new.shipping_code, new.shipping_provider, new.quantity) == ("SPXVN0001", "SPX", 5)
