"""Luật phân loại ĐVVC của đơn — dùng chung cho tile tổng hợp và bộ lọc.

``resolve_carrier`` (Python, gắn nhãn từng đơn) và ``carrier_q`` (Q lọc SQL)
PHẢI cùng luật — sửa một bên phải sửa bên kia. Module riêng để các service
lịch sử (dispatch/return) import mà không tạo vòng với flow_summary_service.
"""
from __future__ import annotations

import re

from django.db.models import Q

OTHER_CARRIER = "Khác"

# provider_code KShip (đã strip + lower) → nhãn hiển thị.
_PROVIDER_LABELS = {
    "ghn": "GHN",
    "giaohangnhanh": "GHN",
    "ghtk": "GHTK",
    "giaohangtietkiem": "GHTK",
    "vtp": "Viettel Post",
    "viettel": "Viettel Post",
    "viettelpost": "Viettel Post",
    # KShip trả code thô "J&T"; "jt"/"jtexpress" giữ cho nguồn khác (import tay, KiotViet cũ).
    "j&t": "J&T Express",
    "jt": "J&T Express",
    "jtexpress": "J&T Express",
    "best": "Best Express",
    "bestexpress": "Best Express",
    "spx": "SPX Express",
    "shopeeexpress": "SPX Express",
    "ninjavan": "Ninja Van",
    "vnpost": "VNPost",
    "ems": "EMS",
}

# Pattern đầu mã vận đơn CHẮC CHẮN (fallback khi provider trống). GHTK dạng
# "S123456.A1.B2"; Best 13 số bắt đầu 77; SPX "SPXVN…"; Ninja Van "NJVN…".
# Regex string dùng chung cho cả Python (re, IGNORECASE) lẫn Postgres (iregex).
_CODE_RULES: list[tuple[str, str]] = [
    (r"^SPXVN", "SPX Express"),
    (r"^GHN", "GHN"),
    (r"^S\d+\.", "GHTK"),
    (r"^77\d{11}$", "Best Express"),
    (r"^JT", "J&T Express"),
    (r"^NJVN", "Ninja Van"),
]
_CODE_RULES_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in _CODE_RULES]

# Danh sách nhãn cho dropdown lọc (mirror ở FE DispatchHistoryPage).
CARRIER_LABELS = sorted({*_PROVIDER_LABELS.values()})


def resolve_carrier(provider: str, shipping_code: str) -> str:
    """Nhãn ĐVVC của 1 đơn; không nhận diện được → "Khác"."""
    key = (provider or "").strip().lower()
    if key:
        return _PROVIDER_LABELS.get(key, OTHER_CARRIER)
    code = (shipping_code or "").strip()
    for pattern, label in _CODE_RULES_COMPILED:
        if pattern.match(code):
            return label
    return OTHER_CARRIER


def carrier_q(label: str, prefix: str = "") -> Q:
    """Q lọc Order theo nhãn ĐVVC — cùng luật với ``resolve_carrier``.

    ``prefix`` để lọc qua quan hệ (vd ``source_order__`` trên ReturnEntry).
    """
    def field(name: str) -> str:
        return f"{prefix}{name}"

    if label == OTHER_CARRIER:
        known = Q()
        for lab in CARRIER_LABELS:
            known |= carrier_q(lab, prefix)
        return ~known

    provider_keys = [k for k, v in _PROVIDER_LABELS.items() if v == label]
    if not provider_keys:
        # Label lạ (API gọi tay): không có provider nào — trả rỗng thay vì regex
        # `^\s*()\s*$` match provider TRỐNG ra kết quả rác (audit 2026-07-18).
        q = Q(pk__in=[])
        for pattern, lab in _CODE_RULES:
            if lab == label:
                q |= Q(**{field("shipping_provider"): ""}) & Q(
                    **{field("shipping_code__iregex"): pattern}
                )
        return q
    q = Q(**{field("shipping_provider__iregex"): r"^\s*({})\s*$".format("|".join(provider_keys))})
    for pattern, lab in _CODE_RULES:
        if lab == label:
            # Pattern mã chỉ áp khi provider trống — provider (nếu có) luôn thắng.
            q |= Q(**{field("shipping_provider"): ""}) & Q(
                **{field("shipping_code__iregex"): pattern}
            )
    return q
