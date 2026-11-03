"""Shared exceptions + hằng số dùng chung cho SyncInvoicesService và các mixin.

Kept in a standalone module so the per-concern mixins can import them without a
cycle back to the main service module.
"""

# TRẦN CỨNG của KiotViet public API ``GET /invoices``: pageSize tối đa 100 (mặc định 20).
# Xin lớn hơn thì server IM LẶNG cắt về 100 — không lỗi, không cảnh báo. Vòng phân trang nào
# dừng theo "trang ngắn hơn số đã xin" sẽ thoát ngay sau trang 1 và quét THIẾU dữ liệu.
# Đúng bug 27/06→26/08: purge/check xin top=500 nên chỉ thấy 100 đơn CŨ NHẤT trong window
# (orderBy=id Asc) → purge_cancelled chỉ bắt 3/55 đơn huỷ.
KIOTVIET_LIST_MAX_PAGE_SIZE = 100


class SyncError(Exception):
    """Tương đương CommandError trong command gốc."""


class _SkipInvoice(Exception):
    """Raised when an invoice should be skipped (not an error)."""
