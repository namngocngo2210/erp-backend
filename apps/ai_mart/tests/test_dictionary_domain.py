"""Từ điển là NGUỒN SỰ THẬT — trùng mã trong 1 nhóm hay nhãn rỗng là hỏng cả mart."""

from apps.ai_mart.domain.dictionary import DICTIONARY, dictionary_rows


def test_no_duplicate_codes_within_group():
    for nhom, items in DICTIONARY.items():
        codes = [ma for ma, _ in items]
        assert len(codes) == len(set(codes)), f"trùng mã trong nhóm {nhom}"


def test_no_empty_labels():
    for nhom, ma, nhan, _ in dictionary_rows():
        assert nhan.strip(), f"nhãn rỗng: {nhom}/{ma}"


def test_core_groups_present():
    # Các nhóm mà transform Sprint 1-3 tra cứu — thiếu 1 nhóm là cột _text ra NULL hàng loạt.
    expected = {
        "trang_thai_don", "trang_thai_hoa_don", "loai_don", "loai_don_koc",
        "nguon_don", "hinh_thuc_giao", "trang_thai_sale", "ly_do_khong_chot",
        "trang_thai_giao_nghiep_vu", "nhom_ly_do_bom", "phan_loai_khach",
        "nghe_nghiep", "muc_do_dau", "nhom_tuoi", "gioi_tinh",
        "trang_thai_ton", "loai_bien_dong_kho", "loai_chung_tu_kho",
        "trang_thai_boc_bang",
    }
    assert expected <= set(DICTIONARY.keys())


def test_order_status_covers_all_enum_values():
    from apps.orders.models import OrderStatus

    codes = {ma for ma, _ in DICTIONARY["trang_thai_don"]}
    assert codes == {str(s.value) for s in OrderStatus}


def test_inventory_and_order_groups_match_enum_value_sets():
    """M4: trang_thai_ton/loai_bien_dong_kho/loai_chung_tu_kho giờ build từ
    enum apps.inventory.models thay vì dict chép tay — parity test này bảo đảm
    mã trong từ điển luôn khớp đúng bộ giá trị enum, không lệch khi enum đổi.
    loai_don/hinh_thuc_giao cũng đối chiếu enum tương ứng của apps.orders.models."""
    from apps.inventory.models import MovementReferenceType, MovementType, StockState
    from apps.orders.models import OrderType, ShippingType

    checks = {
        "trang_thai_ton": StockState,
        "loai_bien_dong_kho": MovementType,
        "loai_chung_tu_kho": MovementReferenceType,
        "loai_don": OrderType,
        "hinh_thuc_giao": ShippingType,
    }
    for nhom, enum_cls in checks.items():
        codes = {ma for ma, _ in DICTIONARY[nhom]}
        assert codes == {m.value for m in enum_cls}, f"lệch enum ở nhóm {nhom}"


def test_nguon_don_matches_order_source_minus_mirror():
    """nguon_don phải khớp toàn bộ OrderSource TRỪ đi các nguồn mirror
    (foxia/cnv) — đơn mirror không phải đơn của công ty (apps.common.order_scope)."""
    from apps.common.order_scope import MIRROR_ORDER_SOURCES
    from apps.orders.models import OrderSource

    codes = {ma for ma, _ in DICTIONARY["nguon_don"]}
    expected = {s.value for s in OrderSource} - set(MIRROR_ORDER_SOURCES)
    assert codes == expected
