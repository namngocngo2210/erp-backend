from __future__ import annotations

from django.utils.timezone import localtime

from apps.warranty.models import WarrantyClaim

_HEADERS = [
    "Mã đơn gốc",
    "Khách",
    "SĐT",
    "Địa chỉ",
    "Mã vận đơn",
    "Trạng thái phiếu",
    "Số hành trình",
    "Ngày khách báo lỗi",
    "Ngày kích hoạt",
    "Ngày hết hạn",
    "Số ngày bảo hành",
    "Ngày tạo",
]


def _fmt_dt(value) -> str:
    if not value:
        return ""
    return localtime(value).strftime("%d/%m/%Y %H:%M")


def _fmt_date(value) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def _fmt_days_left(value) -> str | int:
    """Khớp hiển thị web (warranty-claim-columns.ts): <= 0 → "Hết hạn"."""
    if value is None:
        return ""
    if value <= 0:
        return "Hết hạn"
    return value


def _claim_row(claim: WarrantyClaim) -> list:
    order = claim.order
    return [
        order.invoice_code or "",
        order.name or "",
        order.phone or "",
        order.address or "",
        order.shipping_code or "",
        claim.get_status_display(),
        len(claim.journeys.all()),  # dùng cache prefetch (base_qs), tránh N+1
        _fmt_date(claim.customer_report_date),
        _fmt_date(claim.activation_date),
        _fmt_date(claim.expiry_date),
        _fmt_days_left(claim.warranty_days_left),
        _fmt_dt(claim.created_at),
    ]
