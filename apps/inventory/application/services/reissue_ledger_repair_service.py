"""Quét-bù sổ xuất cho đơn XUẤT LẠI bị bỏ rơi (docs/reissue-ledger-leak-2026-07-21.md).

`transfer_issue_to_sibling` chỉ bắt tại đúng khoảnh khắc webhook huỷ về. Khi hai
luồng KiotViet độc lập đến sai thứ tự (đơn `.NN` chưa kịp có invoice_code/mã vận
đơn lúc huỷ), nhánh đó trượt: đơn huỷ hoàn tồn, đơn `.NN` mang hàng đi giao mà
KHÔNG có bút toán nào → tồn phồng vĩnh viễn, không đường tự khớp lại.

Service này là lưới hứng phía sau: quét cặp lệch rồi ghi sổ xuất bù cho đơn `.NN`,
mirror đúng (kho, sản phẩm, số lượng) mà đơn huỷ đã thả ra.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Sum

from apps.inventory.application.services.reissue_sibling import (
    CANCELLED_STATUS,
    find_live_reissued_sibling,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    CANCEL_RELEASE_NOTE,
    SIBLING_TRANSFER_REISSUE_NOTE,
    StockSalesService,
    lock_order_stock,
    outstanding_by_key,
    real_dispatch_occurred_at,
    returned_by_product,
)
from apps.inventory.models import MovementType, StockMovement, StockState

logger = logging.getLogger(__name__)

_REFERENCE_TYPE = "order"


@dataclass(frozen=True)
class LedgerLeak:
    """Một cặp (đơn huỷ đã thả tồn, đơn xuất lại chưa có sổ) cần ghi bù."""

    cancelled_order_id: int
    sibling_order_id: int
    # (warehouse_id, product_id, quantity) — số lượng đơn huỷ đã thả về tồn.
    lines: tuple[tuple[int, int, int], ...] = field(default_factory=tuple)

    @property
    def quantity(self) -> int:
        return sum(qty for _, _, qty in self.lines)


def _released_lines(order_id: int) -> tuple[tuple[int, int, int], ...]:
    """Phần đơn huỷ đã thả về tồn, gộp theo (kho, sản phẩm).

    Chỉ lấy reversal mang note huỷ đơn — reversal của un-dispatch/resync/chuyển
    sổ mang note KHÁC và không thuộc lỗi này.
    """
    rows = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            reference_id=order_id,
            movement_type=MovementType.SALE_REVERSAL,
            note=CANCEL_RELEASE_NOTE,
        )
        .values("warehouse_id", "product_id")
        .annotate(total=Sum("qty_delta"))
        .order_by("warehouse_id", "product_id")
    )
    return tuple(
        (row["warehouse_id"], row["product_id"], row["total"])
        for row in rows
        if row["total"] > 0
    )


def _transferable_lines(order_id: int) -> tuple[tuple[int, int, int], ...]:
    """Phần outstanding của đơn huỷ sẽ được chuyển sổ, ĐÃ trừ credit phiếu hoàn.

    Mirror đúng vòng tính của ``transfer_issue_to_sibling`` (dry-run không được
    hứa một con số rồi ghi một con số khác).
    """
    outstanding = outstanding_by_key(order_id)
    returned = returned_by_product(order_id)
    credit_used: dict[int, int] = {}
    lines: list[tuple[int, int, int]] = []
    for (warehouse_id, product_id), qty in sorted(outstanding.items()):
        if qty <= 0:
            continue
        used = credit_used.get(product_id, 0)
        credited = min(qty, max(0, returned.get(product_id, 0) - used))
        credit_used[product_id] = used + credited
        move = qty - credited
        if move > 0:
            lines.append((warehouse_id, product_id, move))
    return tuple(lines)


class ReissueLedgerRepairService:
    @staticmethod
    def find_leaks(*, limit: int = 0) -> list[LedgerLeak]:
        """Các cặp đơn huỷ / đơn xuất lại đang để tồn phồng.

        Điều kiện (mọi vế đều bắt buộc):
        - Đơn huỷ đã thả tồn do HUỶ (có reversal note `Hoàn tồn do huỷ đơn`).
        - Có đơn anh em đang sống, cùng luật nhận diện với đường bắt-tại-chỗ.
        - Đơn anh em CHƯA có bất kỳ bút toán nào (chưa ai xuất kho cho nó).

        Số lượng ghi bù lấy từ chính các reversal huỷ đơn nên đã TRỪ SẴN phần
        hàng quay về kho qua phiếu hoàn (`release_for_order` credit trước khi
        thả) — không cần guard riêng cho ca hoàn một phần.
        """
        from apps.orders.models import Order

        released_ids = (
            StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE,
                movement_type=MovementType.SALE_REVERSAL,
                note=CANCEL_RELEASE_NOTE,
            )
            .values_list("reference_id", flat=True)
            .distinct()
        )
        cancelled_ids = (
            Order.objects.filter(
                id__in=released_ids,
                status=CANCELLED_STATUS,
                deleted_at__isnull=True,
            )
            .exclude(invoice_code="")
            .order_by("id")
            .values_list("id", flat=True)
        )

        leaks: list[LedgerLeak] = []
        for order_id in cancelled_ids:
            sibling_id = find_live_reissued_sibling(order_id)
            if sibling_id is None:
                continue
            if StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE, reference_id=sibling_id
            ).exists():
                continue
            lines = _released_lines(order_id)
            if not lines:
                continue
            leaks.append(
                LedgerLeak(
                    cancelled_order_id=order_id,
                    sibling_order_id=sibling_id,
                    lines=lines,
                )
            )
            if limit and len(leaks) >= limit:
                break
        return leaks

    @staticmethod
    def find_outstanding_leaks(*, limit: int = 0) -> list[LedgerLeak]:
        """Dạng leak LUẬT MỚI (huỷ không auto-release, từ 2026-07-29).

        Webhook huỷ đến khi sibling `.NN` CHƯA tồn tại → nhánh chuyển-sổ-tại-chỗ
        trong subscriber trượt, outstanding nằm lại trên đơn huỷ vĩnh viễn còn
        sibling mang hàng đi giao mà không có bút toán nào. Không có
        `CANCEL_RELEASE_NOTE` để lần theo (không còn được ghi) — nhận diện bằng
        chính trạng thái lệch: đơn HUỶ còn outstanding + sibling sống chưa có sổ.
        """
        from apps.orders.models import Order

        outstanding_ids = (
            StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE,
                movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            )
            .values("reference_id")
            .annotate(net=Sum("qty_delta"))
            .filter(net__lt=0)
            .values_list("reference_id", flat=True)
        )
        cancelled_ids = (
            Order.objects.filter(
                id__in=outstanding_ids,
                status=CANCELLED_STATUS,
                deleted_at__isnull=True,
            )
            .exclude(invoice_code="")
            .order_by("id")
            .values_list("id", flat=True)
        )

        leaks: list[LedgerLeak] = []
        for order_id in cancelled_ids:
            sibling_id = find_live_reissued_sibling(order_id)
            if sibling_id is None:
                continue
            if StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE,
                reference_id=sibling_id,
                movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            ).exists():
                continue
            lines = _transferable_lines(order_id)
            if not lines:
                continue
            leaks.append(
                LedgerLeak(
                    cancelled_order_id=order_id,
                    sibling_order_id=sibling_id,
                    lines=lines,
                )
            )
            if limit and len(leaks) >= limit:
                break
        return leaks

    @staticmethod
    @transaction.atomic
    def repair(leak: LedgerLeak, *, user_id: int | None = None) -> bool:
        """Ghi sổ xuất bù cho đơn anh em. Idempotent: đã có sổ thì no-op."""
        for oid in sorted((leak.cancelled_order_id, leak.sibling_order_id)):
            lock_order_stock(oid)
        # Kiểm lại TRONG lock: `find_leaks` chạy ngoài lock nên kết quả có thể đã
        # cũ (worker khác vừa ghi xong). Cùng lý do với `transfer_issue_to_sibling`.
        if StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE, reference_id=leak.sibling_order_id
        ).exists():
            return False
        # Sweep chạy 30' một lần và vá được cặp lệch từ nhiều ngày trước — bút
        # toán bù mang ngày hàng rời kho của đơn huỷ, không phải ngày quét ra.
        dispatched_at = real_dispatch_occurred_at(leak.cancelled_order_id)
        for warehouse_id, product_id, quantity in leak.lines:
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-quantity,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=leak.sibling_order_id,
                note=SIBLING_TRANSFER_REISSUE_NOTE,
                user_id=user_id,
                occurred_at=dispatched_at,
            )
        logger.info(
            "Ghi bù sổ xuất: đơn huỷ %s → đơn xuất lại %s (%s sản phẩm)",
            leak.cancelled_order_id,
            leak.sibling_order_id,
            leak.quantity,
        )
        return True

    @staticmethod
    def sweep(
        *, dry_run: bool = False, limit: int = 0, user_id: int | None = None
    ) -> dict:
        """Quét + ghi bù cả 2 dạng leak. Trả summary cho command/task/cảnh báo Lark.

        - Dạng CŨ (đã auto-release khi huỷ): ghi bù SALE cho sibling (`repair`).
        - Dạng MỚI (outstanding còn nằm trên đơn huỷ): chuyển sổ nguyên cặp qua
          `transfer_issue_to_sibling` — tổng tồn không đổi, chỉ đổi đơn đứng tên.
        Hai tập không giao nhau: đơn đã release thì outstanding = 0.
        """
        legacy = ReissueLedgerRepairService.find_leaks(limit=limit)
        outstanding = ReissueLedgerRepairService.find_outstanding_leaks(limit=limit)
        repaired = 0
        pairs: list[dict] = []
        for leak in legacy:
            row = {
                "cancelled_order_id": leak.cancelled_order_id,
                "sibling_order_id": leak.sibling_order_id,
                "quantity": leak.quantity,
            }
            if dry_run:
                pairs.append(row)
                continue
            if ReissueLedgerRepairService.repair(leak, user_id=user_id):
                repaired += 1
                pairs.append(row)
        for leak in outstanding:
            row = {
                "cancelled_order_id": leak.cancelled_order_id,
                "sibling_order_id": leak.sibling_order_id,
                "quantity": leak.quantity,
            }
            if dry_run:
                pairs.append(row)
                continue
            moved = StockSalesService.transfer_issue_to_sibling(
                order_id=leak.cancelled_order_id,
                sibling_order_id=leak.sibling_order_id,
                user_id=user_id,
            )
            if moved:
                repaired += 1
                pairs.append(row)
        return {
            "dry_run": dry_run,
            "leaks": len(legacy) + len(outstanding),
            "repaired": repaired,
            "pairs": pairs,
        }
