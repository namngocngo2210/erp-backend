"""Chống nhận nhầm tỉnh khi địa chỉ gõ không có dấu phẩy.

Thư viện vietnamadminunits có từ khoá tắt "hn" (Hà Nội) và so khớp trên chuỗi
đã bỏ hết khoảng trắng, nên "…thành phố vinh nghệ an" → "…vin[hn]ghean" bị
bắt thành Hà Nội. Wrapper của ta phải chặn lỗi này mà vẫn hiểu "HN" đứng riêng.
"""

from apps.orders.address_parser import parse_legacy_level3


def test_no_comma_nghe_an_is_not_ha_noi():
    parsed = parse_legacy_level3("Nha 10 ngõ 4 phường hồng sơn thành phố vinh Nghệ An")
    assert parsed["short_province"] == "Nghệ An"
    assert parsed["district"] == "Thành phố Vinh"
    assert parsed["address"] == "Nha 10 Ngõ 4"


def test_hn_inside_word_does_not_imply_ha_noi():
    parsed = parse_legacy_level3("xóm 3 xã Thạch Nham huyện Thạch Hà")
    assert parsed["short_province"] != "Hà Nội"


def test_standalone_hn_token_still_means_ha_noi():
    parsed = parse_legacy_level3("số 5 ngõ 2 phường Thanh Nhàn quận Hai Bà Trưng HN")
    assert parsed["short_province"] == "Hà Nội"
    assert parsed["district"] == "Quận Hai Bà Trưng"


def test_comma_separated_address_unchanged():
    parsed = parse_legacy_level3("12 Lê Lợi, Phường Đại Mỗ, Quận Nam Từ Liêm, Hà Nội")
    assert parsed["short_province"] == "Hà Nội"
    assert parsed["district"] == "Quận Nam Từ Liêm"
    assert parsed["ward"] == "Phường Đại Mỗ"


def test_standalone_hn_beats_unique_district_elsewhere():
    """'Vĩnh Hưng' cũng là huyện của Long An; 'HN' đứng riêng phải thắng."""
    parsed = parse_legacy_level3("198c Vĩnh Hưng, Hoàng Mai, HN")
    assert parsed["short_province"] == "Hà Nội"
    assert parsed["district"] == "Quận Hoàng Mai"
    assert parsed["ward"] == "Phường Vĩnh Hưng"
