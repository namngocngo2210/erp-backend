"""Chuyển đổi thời gian giữa hệ thống ta (Asia/Bangkok) và Foxia (UTC).

Tài liệu Foxia §5: điều kiện ngày trong request dùng UTC, định dạng
``YYYY-MM-DD HH:MM:SS``, không có ký hiệu múi giờ. Ví dụ ngay trong tài liệu:
``2026-07-01T00:00:00+07:00`` phải gửi đi thành ``2026-06-30 17:00:00``.

Toàn bộ chuyển đổi nằm ở ĐÂY và chỉ ở đây. Định dạng ngày rải rác nhiều chỗ là
cách chắc chắn nhất để lệch 7 tiếng ở biên ngày mà không ai nhận ra — báo cáo
vẫn ra số, chỉ là sai.
"""

from __future__ import annotations

from datetime import datetime, UTC

FOXIA_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def to_foxia_utc(value: datetime) -> str:
    """datetime (aware) → chuỗi UTC mà Foxia hiểu."""
    if value.tzinfo is None:
        raise ValueError("to_foxia_utc cần datetime aware — naive là mời gọi lệch múi giờ")
    return value.astimezone(UTC).strftime(FOXIA_DATETIME_FORMAT)


def from_foxia_utc(raw) -> datetime | None:
    """Chuỗi Foxia trả về → datetime aware UTC.

    Odoo trả ``False`` cho ô ngày trống; trả None để caller gán thẳng vào field
    nullable mà không phải kiểm tra thêm.
    """
    if not raw or raw is True:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Odoo trả "YYYY-MM-DD HH:MM:SS"; một số field date-only trả "YYYY-MM-DD".
    for fmt in (FOXIA_DATETIME_FORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
