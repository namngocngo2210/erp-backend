"""Luật map một đơn đã chốt → một conversion event. Pure, không django."""

from __future__ import annotations

from decimal import Decimal

from apps.conversion_feed.domain.postal_codes import PROVINCE_POSTAL_CODES

CURRENCY = "VND"
SKU_SEPARATOR = ";"

# Key tracking CRM chưa lưu — giữ cố định trong contract, trả rỗng (S2 sẽ lấp).
TRACKING_FIELDS: tuple[str, ...] = (
    "ip", "user_agent", "gclid", "gbraid", "wbraid", "fbclid",
    "client_ip_address", "fbp", "madid", "anon_id", "ttclid", "sc_id",
)

# Kênh Google Ads Web: lead landing (hoặc chưa gắn nguồn) chính là lead từ quảng cáo.
DEFAULT_SOURCE = "googleads"
_SOURCE_BY_ORDER_SOURCE: dict[str, str] = {
    "landing": DEFAULT_SOURCE,
    "hotline": "hotline",
    "zalo": "zalo",
    "messenger": "facebook",
}


def split_name(full_name: str) -> tuple[str, str]:
    """Tên kiểu VN "Họ Đệm Tên" → (first_name, last_name) = ("Đệm Tên", "Họ").

    Một từ duy nhất coi là tên (first_name) — khách hay để "Lan", "Anh Tuấn".
    """
    parts = str(full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[1:]), parts[0]


def map_source(order_source: str | None) -> str:
    code = str(order_source or "").strip().lower()
    if not code:
        return DEFAULT_SOURCE
    return _SOURCE_BY_ORDER_SOURCE.get(code, code)


def postal_code(canonical_province: str) -> str:
    return PROVINCE_POSTAL_CODES.get(canonical_province, "")


def join_skus(item_skus: str | None, fallback: str) -> str:
    """SKU các dòng hàng (đã gom ``;`` ở SQL) — đơn chưa có dòng thì lấy ``orders.sku``."""
    text = str(item_skus or "").strip()
    return text or str(fallback or "").strip()


def to_vnd(amount: Decimal | int | None) -> int:
    return int(amount or 0)
