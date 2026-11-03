"""Chuẩn hoá trạng thái thô: giữ nguyên nội dung, chỉ dọn hình thức."""

import pytest

from apps.foxia.contracts.dtos import many2one_id, many2one_name
from apps.foxia.domain.status_text import MAX_LENGTH, normalize_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Đang giao", "đang giao"),
        ("  GIAO THÀNH CÔNG  ", "giao thành công"),
        ("Không   giao\tđược", "không giao được"),
        # many2one: lấy tên hiển thị, không lấy id.
        ([3, "Đã nhận hoàn"], "đã nhận hoàn"),
        # Odoo trả False cho giá trị rỗng.
        (False, ""),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


def test_unknown_code_is_kept_verbatim():
    """Mã lạ vẫn được lưu — lưu thô là để về sau map lại được, không phải để lọc."""
    assert normalize_status("XYZ_TRANG_THAI_LA") == "xyz_trang_thai_la"


def test_value_is_truncated_to_column_width():
    assert len(normalize_status("x" * 200)) == MAX_LENGTH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [([12, "Chi nhánh"], 12), (12, 12), ("12", 12), (False, None), (None, None), ([], None)],
)
def test_many2one_id(raw, expected):
    assert many2one_id(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [([12, "Chi nhánh"], "Chi nhánh"), ("Tên thẳng", "Tên thẳng"), (False, ""), ([12], "")],
)
def test_many2one_name(raw, expected):
    assert many2one_name(raw) == expected
