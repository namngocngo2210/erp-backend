"""Shared test fixtures for dictionary coverage.

Defines PAIRS of (code_column, label_column) pairs used by:
- tests/test_dictionary.py: Verifies all codes in ai.don_hang have translations
- Future ai_mart_verify command: Audit all dictionary completeness
"""

# (cột mã, cột nhãn) closed-set trong ai.don_hang — phuong_thuc_tt là text tự do, không kiểm.
PAIRS = [
    ("trang_thai_don_ma::text", "trang_thai_don_text"),
    ("trang_thai_hoa_don_ma::text", "trang_thai_hoa_don_text"),
    ("trang_thai_sale_ma", "trang_thai_sale_text"),
    ("ly_do_khong_chot_ma", "ly_do_khong_chot_text"),
    ("trang_thai_giao_nghiep_vu_ma", "trang_thai_giao_nghiep_vu_text"),
    ("nhom_ly_do_bom_ma", "nhom_ly_do_bom_text"),
    ("phan_loai_khach_ma", "phan_loai_khach_text"),
    ("nghe_nghiep_ma", "nghe_nghiep_text"),
    ("muc_do_dau_ma", "muc_do_dau_text"),
    ("loai_don_ma", "loai_don_text"),
    ("loai_don_koc_ma", "loai_don_koc_text"),
    ("hinh_thuc_giao_ma", "hinh_thuc_giao_text"),
]
