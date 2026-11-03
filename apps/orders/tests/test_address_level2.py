"""Địa chỉ 2 cấp (34 tỉnh) lưu trên đơn + tên Khu vực/Phường xã KiotViet theo cấu trúc mới."""

from apps.orders.address_level2 import (
    LEVEL2_FIELDS,
    empty_level2_fields,
    resolve_kiotviet_names,
    resolve_level2_fields,
)
from apps.orders.address_parser import parse_legacy_level3


def test_level2_fields_from_old_3_level_address():
    raw = "Số 5 ngõ 12 phố Chùa Láng, Láng Thượng, Đống Đa, Hà Nội"
    fields = resolve_level2_fields(raw, parse_legacy_level3(raw))

    assert set(fields) == set(LEVEL2_FIELDS)
    assert fields["province_level2"] == "Thành phố Hà Nội"
    assert fields["ward_level2"] == "Phường Láng"
    assert fields["ward_code_level2"]
    assert fields["province_code_level2"] == "01"
    assert fields["address_level2"].endswith("Phường Láng, Thành phố Hà Nội")
    assert "Quận" not in fields["address_level2"]


def test_level2_fields_from_address_already_in_new_format():
    raw = "12 Lê Lợi, phường xuân hòa, tp hồ chí minh"
    fields = resolve_level2_fields(raw, parse_legacy_level3(raw))

    assert fields["ward_level2"] == "Phường Xuân Hòa"
    assert fields["province_level2"] == "Thành phố Hồ Chí Minh"


def test_level2_fields_empty_for_garbage_or_blank():
    assert resolve_level2_fields("", parse_legacy_level3("")) == empty_level2_fields()
    assert resolve_level2_fields("abc xyz", parse_legacy_level3("abc xyz")) == empty_level2_fields()


def test_kiotviet_names_prefer_new_province_and_ward():
    """Khu vực = tỉnh mới, Phường/Xã = phường mới → hoá đơn Kiot ra địa chỉ 2 cấp."""
    raw = "Thôn 3, xã Ea Kly, huyện Krông Pắc, Đắk Lắk"
    parsed = parse_legacy_level3(raw)
    level2 = resolve_level2_fields(raw, parsed)

    names = resolve_kiotviet_names(parsed, raw, level2)

    assert names == {"locationName": "Tỉnh Đắk Lắk", "wardName": "Xã Ea Kly"}


def test_kiotviet_names_match_dong_nai_as_thanh_pho():
    """Kiot gọi Đồng Nai là "Thành phố Đồng Nai" (kiểm /locations 3 kênh 20/09/2026) còn bộ
    quy đổi trả "Tỉnh Đồng Nai" → khớp bỏ tiền tố, không rớt về "Đồng Nai - Thành phố Biên Hòa"."""
    raw = "12 Nguyễn Ái Quốc, Phường Trảng Dài, Thành phố Biên Hòa, Đồng Nai"
    parsed = parse_legacy_level3(raw)
    level2 = resolve_level2_fields(raw, parsed)
    assert level2["province_level2"] == "Tỉnh Đồng Nai"

    names = resolve_kiotviet_names(parsed, raw, level2)

    assert names == {"locationName": "Thành phố Đồng Nai", "wardName": "Phường Trảng Dài"}


def test_level2_quang_ninh_is_thanh_pho():
    """Quảng Ninh lên thành phố; thư viện còn trả "Tỉnh Quảng Ninh". Kiot vẫn khớp danh mục."""
    raw = "Số 8 Lê Thánh Tông, Phường Hồng Gai, Thành phố Hạ Long, Quảng Ninh"
    parsed = parse_legacy_level3(raw)
    level2 = resolve_level2_fields(raw, parsed)

    assert level2["province_level2"] == "Thành phố Quảng Ninh"
    assert level2["address_level2"].endswith(", Thành phố Quảng Ninh")
    assert "Tỉnh Quảng Ninh" not in level2["address_level2"]

    names = resolve_kiotviet_names(parsed, raw, level2)

    assert names["locationName"] == "Tỉnh Quảng Ninh"
    assert names["wardName"] == level2["ward_level2"]


def test_kiotviet_names_fall_back_to_3_level_when_level2_missing_ward():
    """Không quy đổi được phường mới → giữ cách cũ "Tỉnh - Quận/Huyện" để không mất khu vực."""
    raw = "123 Nguyễn Trãi, Quận 5, TP.HCM"
    parsed = parse_legacy_level3(raw)
    level2 = resolve_level2_fields(raw, parsed)
    assert level2["province_level2"] == "Thành phố Hồ Chí Minh"
    assert level2["ward_level2"] == ""

    names = resolve_kiotviet_names(parsed, raw, level2)

    assert names["locationName"] == "Hồ Chí Minh - Quận 5"


def test_kiotviet_names_fall_back_when_new_ward_not_in_catalog():
    """Phường mới không có trong danh mục Kiot (lệch chính tả) → không gửi tên lạ lên Kiot."""
    raw = "Thôn 1, xã Ea Kly, huyện Krông Pắc, Đắk Lắk"
    parsed = parse_legacy_level3(raw)
    level2 = resolve_level2_fields(raw, parsed)
    level2["ward_level2"] = "Xã Không Tồn Tại"

    names = resolve_kiotviet_names(parsed, raw, level2)

    assert names["locationName"] == "Đắk Lắk - Huyện Krông Pắc"
    assert names["wardName"] == "Xã Ea Kly"


def test_level2_edit_fills_ward_province_and_kiotviet_names():
    """Sale gõ thẳng địa chỉ 2 cấp → tách tỉnh mới/phường mới + Khu vực/Phường xã Kiot."""
    from apps.orders.address_level2 import resolve_level2_edit

    result = resolve_level2_edit("Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk")

    # Giữ nguyên chuỗi người dùng gõ, chỉ điền các cột suy ra.
    assert result["address_level2"] == "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk"
    assert result["province_level2"] == "Tỉnh Đắk Lắk"
    assert result["ward_level2"] == "Xã Ea Kly"
    assert result["ward_code_level2"]
    assert result["province_code_level2"]
    assert result["kiotviet_location"] == "Tỉnh Đắk Lắk"
    assert result["kiotviet_ward"] == "Xã Ea Kly"


def test_level2_edit_unmatched_leaves_kiotviet_blank():
    """Không tra được phường mới → Khu vực/Phường xã rỗng để caller giữ giá trị cũ."""
    from apps.orders.address_level2 import resolve_level2_edit

    result = resolve_level2_edit("abc xyz")

    assert result["address_level2"] == "abc xyz"
    assert result["ward_level2"] == ""
    assert result["province_level2"] == ""
    assert result["kiotviet_location"] == ""
    assert result["kiotviet_ward"] == ""


def test_level2_edit_blank_clears_everything():
    from apps.orders.address_level2 import empty_level2_fields, resolve_level2_edit

    result = resolve_level2_edit("   ")

    assert {k: result[k] for k in empty_level2_fields()} == empty_level2_fields()
    assert result["kiotviet_location"] == ""
