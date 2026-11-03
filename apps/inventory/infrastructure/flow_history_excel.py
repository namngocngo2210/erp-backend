from __future__ import annotations

from django.utils.timezone import localtime


def _fmt_dt(value):
    if not value:
        return ""
    return localtime(value).strftime("%d/%m/%Y %H:%M")


def _pending_mark(p: dict) -> str:
    return "Lỗi khớp" if p.get("status") == "failed" else "Chờ khớp"


def _truncation_rows(
    headers: list[str], truncated: int | None, included: int
) -> list[list]:
    """Dòng cảnh báo khi kỳ lọc vượt trần xuất file (`truncated` = tổng dòng kỳ).

    Các dòng Tổng (SKU ghi sổ) vẫn aggregate TOÀN BỘ kỳ (đúng chủ đích — người
    dùng cần con số thật), nên khi phần dòng bị cắt thì PHẢI nói ra, không thì
    cộng cột ≠ tổng và không ai hiểu vì sao. Riêng dòng "Tổng SL các dòng trong
    file" KHÔNG nằm trong lời cảnh báo này — nó chỉ cộng những dòng thực sự có
    trong file nên tự nó đã phản ánh đúng phần bị cắt. Caller (view) quyết định
    có cắt hay không; ở đây chỉ nêu sự thật: tổng kỳ vs số dòng thực có trong
    file.
    """
    if not truncated or truncated <= included:
        return []
    message = (
        f"⚠️ Kỳ lọc có {truncated} dòng — file chỉ chứa {included} dòng đầu; "
        "các dòng Tổng (SKU ghi sổ) vẫn tính TOÀN BỘ kỳ. Thu hẹp khoảng lọc để xuất đủ."
    )
    return [[message] + [""] * (len(headers) - 1)]


def _display_qty(net, gross=None) -> int:
    """SL hiển thị của một dòng — file Excel phải ra đúng con số trên màn hình.

    Luật nằm ở ``dispatch_history_service.display_qty`` (một nguồn duy nhất cho
    cả tổng chạy trên DB); ở đây chỉ chuẩn hoá ``None`` của payload.
    """
    from apps.inventory.application.services.dispatch_history_service import (
        display_qty,
    )

    return display_qty(net or 0, gross or 0)


def _issue_order_code(r: dict) -> str:
    """Cột 'Mã hóa đơn': dòng phiếu kho/chuyển kho hiện nhãn phiếu; dòng đơn hiện mã hóa đơn."""
    if r.get("kind") == "document":
        return f"Phiếu xuất kho {r.get('doc_code') or ''}".strip()
    if r.get("kind") == "transfer":
        return f"Phiếu chuyển kho {r.get('doc_code') or ''}".strip()
    return r.get("invoice_code") or r.get("order_code") or ""
