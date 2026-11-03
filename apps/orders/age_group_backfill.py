"""Parse `age_text` tự do (dữ liệu cũ) → mã nhóm tuổi.

Dùng bởi data migration 0110 và bởi `import_assessment_csv` (file CSV cũ vẫn
ghi tuổi dạng số). Thuần Python, không import Django, để migration gọi được
mà không kéo theo app registry.
"""
from __future__ import annotations

import re

# Backfill chạy một lần trên dữ liệu chốt ngày 2026-07-27. Mốc cố định thay vì
# date.today() để chạy lại lúc nào cũng ra cùng kết quả.
REFERENCE_YEAR = 2026

MIN_AGE = 18
MAX_AGE = 100

# Không đặt tên "unknown": report_fields.UNKNOWN_CODE đã chiếm chuỗi đó cho
# nghĩa "chưa điền", còn đây là "đã hỏi nhưng khách không nói".
AGE_GROUP_NOT_DISCLOSED = "not_disclosed"

# (code, tuổi lớn nhất còn thuộc nhóm). Nhóm cuối không có trần.
_UPPER_BOUNDS: tuple[tuple[str, int], ...] = (
    ("18_24", 24),
    ("25_34", 34),
    ("35_44", 44),
    ("45_54", 54),
    ("55_64", 64),
)
_TOP_CODE = "65_plus"

_RANGE_RE = re.compile(r"^(\d{1,4})\s*[-–]\s*(\d{1,4})$")
_DECADE_RE = re.compile(r"^(\d)0?x$")
_DIGITS_RE = re.compile(r"\d+")
_T_SUFFIX_RE = re.compile(r"(?<=\d)t")


def _normalize(raw: str) -> str:
    """Lowercase, trim, gộp khoảng trắng, bỏ 't' (= 'tuổi') sau bất kì chữ số nào.

    Ví dụ: "35t-45t" → "35-45" (cần bỏ cả hai để range rule chạy).
    """
    text = " ".join(str(raw).strip().lower().split())
    return _T_SUFFIX_RE.sub("", text).strip()


def group_for_age(age: int) -> str:
    """Map một số tuổi vào nhóm. Ngoài [18, 100] coi như không xác định."""
    if age < MIN_AGE or age > MAX_AGE:
        return AGE_GROUP_NOT_DISCLOSED
    for code, upper in _UPPER_BOUNDS:
        if age <= upper:
            return code
    return _TOP_CODE


def parse_age_group(raw: str | None) -> str:
    """Chuỗi tự do → mã nhóm tuổi.

    Rỗng trả về rỗng: "sale chưa hỏi" là trạng thái riêng, không phải
    "khách không nói".

    Fixed-point bắt buộc: mọi mã trong catalog (7 mã, xem crm_catalog.
    AGE_GROUP_LABELS) phải parse ra chính nó. Đây là điều kiện để migration
    0110 idempotent (chạy lại không đổi dữ liệu đã convert) và để
    import_assessment_csv import lại được file CSV export đã convert mà
    không làm hỏng dữ liệu. Thêm bracket tuổi thứ 8 mà phá vỡ tính chất này
    sẽ âm thầm làm hỏng cả hai.
    """
    text = _normalize(raw or "")
    if not text:
        return ""

    digits = _DIGITS_RE.search(text)
    if digits is None:
        # Mọi cách diễn đạt bằng lời ("ko chia sẻ", "trung niên") rơi vào đây —
        # bắt theo "không có chữ số" thay vì liệt kê từ khoá để không bao giờ sót.
        return AGE_GROUP_NOT_DISCLOSED

    range_match = _RANGE_RE.match(text)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return group_for_age((low + high) // 2)

    decade_match = _DECADE_RE.match(text)
    if decade_match:
        # "7x" = 70..79 → midpoint 74.
        return group_for_age(int(decade_match.group(1)) * 10 + 4)

    number = int(digits.group())
    if 1900 <= number <= 2015:
        return group_for_age(REFERENCE_YEAR - number)
    return group_for_age(number)
