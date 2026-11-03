"""Số tổng hợp đầu trang Lịch sử nhập xuất + phân loại đơn theo ĐVVC.

Tổng ăn theo CÙNG bộ lọc (ngày/kho/sản phẩm/mã/ĐVVC) với 2 tab bên dưới nên số
tile luôn khớp footer từng tab. Luật ĐVVC ở ``carrier_rules``.
"""
from __future__ import annotations

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.inventory.application.services.carrier_rules import (
    OTHER_CARRIER,
    resolve_carrier,
)

from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.issue_document_history import (
    IssueDocumentHistoryService,
)
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.application.services.transfer_document_history import (
    TransferDocumentHistoryService,
)
from apps.inventory.models import ReturnNoteLine
from apps.orders.models import Order

class FlowSummaryService:
    @staticmethod
    def summary(**filters) -> dict:
        # ── Tab Xuất ──
        sale_quantity = DispatchHistoryService.total_issued(**filters)
        doc_quantity = IssueDocumentHistoryService.total_issued(**filters)
        transfer_out = TransferDocumentHistoryService.total_out(**filters)

        # SL xuất ghi nhận theo từng đơn — cùng luật với dòng trong bảng và với
        # tile tổng (``display_qty``). Đơn xuất rồi huỷ hết trong kỳ có net = 0
        # nhưng vẫn là một lần xuất, phải được đếm ở đây; nếu không tile "ĐƠN ĐÃ
        # XUẤT" nói 0 trong khi bảng bên dưới liệt kê 1 dòng.
        per_order: dict[int, int] = {}
        for r in DispatchHistoryService.order_groups(**filters):
            if r["issued"] > 0:
                per_order[r["reference_id"]] = r["issued"]

        carriers: dict[str, dict] = {}
        if per_order:
            rows = Order.objects.filter(id__in=list(per_order.keys())).values(
                "id", "shipping_provider", "shipping_code"
            )
            for o in rows:
                label = resolve_carrier(o["shipping_provider"], o["shipping_code"])
                bucket = carriers.setdefault(
                    label, {"label": label, "order_count": 0, "quantity": 0}
                )
                bucket["order_count"] += 1
                bucket["quantity"] += per_order[o["id"]]
        carrier_rows = sorted(
            carriers.values(),
            # "Khác" luôn xếp cuối cho dễ đọc, còn lại theo SL giảm dần.
            key=lambda c: (c["label"] == OTHER_CARRIER, -c["quantity"], c["label"]),
        )

        # ── Tab Nhập hoàn ──
        return_entry_qs = ReturnHistoryService._base(**filters)
        # Luật per-SKU khi lọc SKU — dùng chung helper để tile khớp footer tab
        # (audit 2026-07-18).
        return_quantity = ReturnHistoryService.total_returned_quantity(**filters)
        return_count = return_entry_qs.count()
        transfer_in = TransferDocumentHistoryService.total_in(**filters)

        return {
            "issue": {
                "total_quantity": sale_quantity + doc_quantity + transfer_out,
                "sale_quantity": sale_quantity,
                "doc_quantity": doc_quantity,
                "transfer_quantity": transfer_out,
                "order_count": len(per_order),
            },
            "return": {
                "total_quantity": return_quantity + transfer_in,
                "return_quantity": return_quantity,
                "transfer_quantity": transfer_in,
                "return_count": return_count,
            },
            "carriers": carrier_rows,
        }
