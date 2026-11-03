"""Kiểm file upload trước khi đưa vào parser (audit 2026-09-23 L4).

Import Excel ở inventory/contact_flow từng nhận file với trần duy nhất là nginx
`client_max_body_size 50m` (DATA_UPLOAD_MAX_MEMORY_SIZE không áp cho file) và
không kiểm đuôi/magic — một file .zip 50MB đặt tên .xlsx đủ làm openpyxl giải
nén tốn CPU. Mẫu đúng có sẵn ở media (`MAX_UPLOAD_BYTES` 5MB) và phiếu kho
(sniff magic bytes); gom về một chỗ để endpoint import mới dùng chung.

Chỉ trả chuỗi lỗi (tiếng Việt, đưa thẳng vào `{"detail": ...}`), không raise —
view quyết định mã trạng thái.
"""

from __future__ import annotations

XLSX_MAX_BYTES = 5 * 1024 * 1024
XLSX_EXTENSIONS = (".xlsx", ".xlsm")
# .xlsx là container ZIP: 4 byte đầu luôn là local-file-header của ZIP.
_ZIP_MAGIC = b"PK\x03\x04"


def validate_xlsx_upload(upload, *, max_bytes: int | None = None) -> str | None:
    """Trả None nếu hợp lệ, ngược lại trả message lỗi cho client.

    Kiểm theo thứ tự rẻ → đắt: kích thước (thuộc tính, không đọc), đuôi tên
    file, rồi 4 byte magic (seek về 0 sau khi đọc để parser đọc lại từ đầu).
    ``max_bytes`` None = đọc XLSX_MAX_BYTES lúc gọi (test patch được hằng module).
    """
    if max_bytes is None:
        max_bytes = XLSX_MAX_BYTES
    size = getattr(upload, "size", None)
    if size and size > max_bytes:
        return f"File quá lớn (tối đa {max(max_bytes // (1024 * 1024), 1)}MB)."
    name = str(getattr(upload, "name", "") or "").lower()
    if name and not name.endswith(XLSX_EXTENSIONS):
        return "Chỉ chấp nhận file Excel .xlsx."
    try:
        head = upload.read(len(_ZIP_MAGIC))
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass
    if head != _ZIP_MAGIC:
        return "File không phải định dạng Excel .xlsx hợp lệ."
    return None
