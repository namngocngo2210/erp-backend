"""Parse ngày từ tham số ngoài (query param, filter export, HTML cào).

Gom 6 bản ``_parse_date`` từng chép tay ở callcenter / orders / warranty_codes /
kiotviet (audit 2026-09-23 T3/P2-5). Module này CỐ Ý không import Django/DRF
để tầng domain cũng dùng được; dịch ``ValueError`` sang ValidationError với
thông báo riêng là việc của caller (mỗi endpoint giữ đúng message cũ của nó).

Quy ước chung cho mọi hàm: giá trị rỗng (``None`` / chuỗi trắng) → ``None``,
KHÔNG bao giờ raise — mọi caller cũ đều coi "không truyền" là "không lọc".
"""

from __future__ import annotations

from datetime import date, datetime

ISO_DATE_FORMAT = "%Y-%m-%d"
DDMMYYYY_FORMAT = "%d/%m/%Y"


def _parse_with_format(raw, fmt: str, *, strict: bool) -> date | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        # Giá trị JSON không phải chuỗi (số, list, datetime…) — bản cũ nào cũng từ chối.
        if strict:
            raise ValueError(f"Ngày phải là chuỗi định dạng {fmt}, nhận {type(raw).__name__}")
        return None
    try:
        return datetime.strptime(raw.strip(), fmt).date()
    except ValueError as exc:
        if strict:
            raise ValueError(f"Ngày không đúng định dạng {fmt}: {raw!r}") from exc
        return None


def parse_iso_date(raw, *, strict: bool = False) -> date | None:
    """``"YYYY-MM-DD"`` → ``date``; rỗng → ``None``.

    - ``strict=False`` (mặc định): chuỗi hỏng → ``None`` (bộ lọc chỉ-đọc bỏ qua
      tham số hỏng thay vì chặn người dùng).
    - ``strict=True``: chuỗi hỏng hoặc kiểu không phải chuỗi → ``ValueError``;
      caller dịch sang 400 với thông báo của mình.

    Không nhận datetime ISO (``2026-01-01T00:00``) hay giá trị không phải chuỗi:
    mọi bản cũ đều từ chối các dạng này.
    """
    return _parse_with_format(raw, ISO_DATE_FORMAT, strict=strict)


def parse_ddmmyyyy(raw, *, strict: bool = False) -> date | None:
    """``"dd/mm/yyyy"`` (định dạng KiotViet hiển thị) → ``date``; hỏng → ``None``."""
    return _parse_with_format(raw, DDMMYYYY_FORMAT, strict=strict)
