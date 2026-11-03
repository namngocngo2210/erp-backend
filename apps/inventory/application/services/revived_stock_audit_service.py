"""Đối soát tồn của đơn HUỶ được hồi sinh (docs/erp-revive-stock-safety-net.md).

Khi đơn bị huỷ, subscriber kho hoàn tồn về (`Hoàn tồn do huỷ đơn`). Khi đơn sống lại,
`reissue_after_cancel_release` phải trừ lại đúng phần đó — hàng vẫn đang ngoài kho. Sự cố
2026-07-23: 2 đơn hồi sinh mà bước trừ lại KHÔNG chạy, tồn phồng vĩnh viễn và không ai
biết cho tới khi có người đi tìm.

Nhánh hồi sinh chạy trong `on_commit` handler nên lỗi bị bus nuốt; dấu vết duy nhất là một
dòng log, mất theo container mỗi lần deploy. Service này là lưới hứng: đối chiếu thẳng sổ
movement, không phụ thuộc sự kiện có tới được handler hay không — nên nó bắt được cả những
đường hụt mà ta CHƯA biết.

CHỈ phát hiện, KHÔNG tự ghi sổ. Tự động trừ tồn dựa trên suy đoán chính là loại rủi ro đã
đẻ ra sự cố này; để người xác nhận rồi chạy `reissue_after_cancel_release` (idempotent).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Q, Sum

from apps.inventory.application.services.stock_sales_service import (
    AUTO_RELEASE_RECLAIM_NOTE,
    CANCEL_RELEASE_NOTE,
    REVIVE_REISSUE_NOTE,
)
from apps.inventory.models import MovementType, StockMovement

logger = logging.getLogger(__name__)

_REFERENCE_TYPE = "order"
_CANCELLED_STATUS = 6


@dataclass(frozen=True)
class RevivedOrderLeak:
    """Một đơn đã hồi sinh mà phần tồn hoàn lúc huỷ chưa được trừ lại."""

    order_id: int
    order_code: str
    shipping_code: str
    status: int
    quantity: int


class RevivedStockAuditService:
    @staticmethod
    def find_unreissued(*, limit: int = 0) -> list[RevivedOrderLeak]:
        """Đơn còn sống, từng được hoàn tồn do huỷ, mà chưa được trừ lại.

        Dùng ĐÚNG công thức của `reissue_after_cancel_release`: cộng mọi `SALE_REVERSAL`
        do huỷ (note rỗng = release từ subscriber đời đầu, hoặc `CANCEL_RELEASE_NOTE`),
        trừ đi phần đã bù (`SALE` mang `REVIVE_REISSUE_NOTE`), gộp theo (kho, sản phẩm).
        Dư > 0 = tồn đang phồng.

        Không tự nghĩ ra tiêu chí riêng: detector và fixer PHẢI cùng một công thức, nếu
        không sẽ có ngày cảnh báo một đằng, chạy sửa lại không ghi gì.
        """
        from apps.orders.models import Order

        # Ứng viên: mọi đơn từng bị hoàn tồn do huỷ. Lọc trước ở tầng SQL để không kéo
        # cả sổ về Python — index `stockmove_ref_type_created_idx` phủ (reference_type,
        # movement_type).
        released_ids = (
            StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE,
                movement_type=MovementType.SALE_REVERSAL,
                note__in=("", CANCEL_RELEASE_NOTE),
            )
            .values_list("reference_id", flat=True)
            .distinct()
        )
        live_orders = {
            row["id"]: row
            for row in Order.objects.filter(id__in=released_ids, deleted_at__isnull=True)
            .exclude(status=_CANCELLED_STATUS)
            .values("id", "order_code", "shipping_code", "status")
        }
        if not live_orders:
            return []

        rows = (
            StockMovement.objects.filter(
                Q(
                    movement_type=MovementType.SALE_REVERSAL,
                    note__in=("", CANCEL_RELEASE_NOTE),
                )
                # Phần bù gồm CẢ reclaim của phiếu hoàn legacy — PHẢI cùng công
                # thức với `pending_cancel_release_by_key` (fixer). Thiếu
                # AUTO_RELEASE_RECLAIM_NOTE thì đơn legacy đã reclaim bị kêu
                # "leak" mỗi đêm vĩnh viễn trong khi fixer (đúng công thức) no-op.
                | Q(
                    movement_type=MovementType.SALE,
                    note__in=(REVIVE_REISSUE_NOTE, AUTO_RELEASE_RECLAIM_NOTE),
                ),
                reference_type=_REFERENCE_TYPE,
                reference_id__in=list(live_orders),
            )
            .values("reference_id", "warehouse_id", "product_id")
            .annotate(net=Sum("qty_delta"))
        )
        pending_by_order: dict[int, int] = defaultdict(int)
        for row in rows:
            # Chỉ cộng phần dư DƯƠNG theo từng (kho, sản phẩm) — giống vòng lặp của
            # `reissue_after_cancel_release`. Cộng cả phần âm sẽ để một sản phẩm đã bù
            # dư che mất một sản phẩm chưa bù.
            if row["net"] > 0:
                pending_by_order[row["reference_id"]] += row["net"]

        leaks = [
            RevivedOrderLeak(
                order_id=order_id,
                order_code=live_orders[order_id]["order_code"] or "",
                shipping_code=live_orders[order_id]["shipping_code"] or "",
                status=int(live_orders[order_id]["status"] or 0),
                quantity=quantity,
            )
            for order_id, quantity in sorted(pending_by_order.items())
            if quantity > 0
        ]
        return leaks[:limit] if limit else leaks
