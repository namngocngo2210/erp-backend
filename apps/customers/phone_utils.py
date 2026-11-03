"""Shim tương thích — module đã chuyển sang ``apps.common.phone_utils``.

DEPRECATED (audit 2026-09-23, C3/P1-5): chuẩn hoá SĐT là tiện ích dùng chung của
13 app, không phải nghiệp vụ `customers`; đặt ở đây làm `orders.models` phải
lazy-import ngược tầng. Import từ ``apps.common.phone_utils``; shim này giữ lại
để migration cũ (`warranty/migrations/0022`) và code ngoài repo không gãy, sẽ xoá sau.
"""

from apps.common.phone_utils import (  # noqa: F401 — re-export có chủ đích
    normalize_phone,
    phone_lookup_candidates,
    phone_search_candidates,
)
