"""Vòng đời hàng đợi 'đơn huỷ chờ hàng về'.

Ledger là nguồn số; service này chỉ quản dòng theo dõi. Xem spec
docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §5.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventory.application.services.flow_note_service import FlowNoteService
from apps.inventory.application.services.reissue_sibling import (
    find_live_reissued_sibling,
)
from apps.inventory.application.services.stock_sales_service import (
    SIBLING_TRANSFER_RELEASE_NOTE,
    order_goods_still_out,
    returned_by_product,
)
from apps.inventory.models import (
    CancelledDispatchWatch,
    CloseReason,
    MovementType,
    StockFlowNoteTarget,
    StockMovement,
    WatchStatus,
)

logger = logging.getLogger(__name__)


def _is_reissue_closed(watch: CancelledDispatchWatch) -> bool:
    """Dòng đã đóng vì sổ xuất chuyển sang đơn xuất lại — nhãn này KHÔNG được ghi đè.

    Đơn hết nợ vì đổi đơn đứng tên, không phải vì hàng về kho: để `refresh_for_order`
    / `settle_if_returned` lật sang RETURNED là báo cáo "hàng đã về" cho lô hàng chưa
    hề quay lại. Còn nợ kho trở lại thì `_reopen` vẫn chạy như thường.
    """
    return (
        watch.status == WatchStatus.CLOSED
        and watch.closed_reason == CloseReason.REISSUED
    )


class AwaitingReturnService:
    """Đơn huỷ mà hàng còn ngoài kho — mở, đóng, và tra danh sách."""

    # Lý do người dùng được chọn khi đóng tay. `CloseReason.REISSUED` cố tình NẰM
    # NGOÀI: đó là nhãn hệ thống tự ghi khi sổ xuất chuyển sang đơn xuất lại, cho
    # người dùng chọn thì watch bị đóng sai trong khi hàng vẫn thật sự ngoài kho.
    MANUAL_CLOSE_REASONS = frozenset(CloseReason.values) - {CloseReason.REISSUED}

    @staticmethod
    @transaction.atomic
    def open_for_order(order_id: int) -> bool:
        """Mở dòng theo dõi cho đơn vừa huỷ mà hàng còn ngoài kho. Idempotent.

        Trả về ``True`` chỉ khi dòng được mở MỚI — đây là tín hiệu "vừa phát hiện
        một đơn đã xuất kho bị huỷ", dùng để bắn cảnh báo Lark cho kho đúng một
        lần. Sự kiện huỷ bắn lại (webhook KiotViet gửi trùng) hay dòng cũ được mở
        lại theo sổ đều trả ``False``: hàng đó kho đã biết rồi.
        """
        from apps.orders.models import Order  # local import: giữ ranh giới module

        if not order_goods_still_out(order_id):
            return False
        order = (
            Order.objects.filter(pk=order_id).values("fulfillment_warehouse_id").first()
        )
        if order is None or not order["fulfillment_warehouse_id"]:
            logger.warning("Đơn %s huỷ khi đã xuất nhưng không có kho giao", order_id)
            return False
        watch, created = CancelledDispatchWatch.objects.get_or_create(
            order_id=order_id,
            defaults={
                "warehouse_id": order["fulfillment_warehouse_id"],
                "cancelled_at": timezone.now(),
                "status": WatchStatus.WAITING,
            },
        )
        # Dòng cũ đang RETURNED/CLOSED nhưng hàng lại NỢ kho (phiếu hoàn bị huỷ
        # → bút toán đảo): get_or_create không reset thì đơn biến mất khỏi danh
        # sách đòi hàng vĩnh viễn — sự thật của sổ phải thắng nhãn cũ.
        if not created and watch.status != WatchStatus.WAITING:
            AwaitingReturnService._reopen(watch)
        return created

    @staticmethod
    def _reopen(watch: CancelledDispatchWatch) -> None:
        watch.status = WatchStatus.WAITING
        watch.closed_reason = ""
        watch.closed_note = ""
        watch.closed_by = None
        watch.closed_at = None
        watch.save(
            update_fields=[
                "status", "closed_reason", "closed_note",
                "closed_by", "closed_at", "updated_at",
            ]
        )
        logger.info("Đơn %s: hàng lại nợ kho, mở lại dòng theo dõi", watch.order_id)

    @staticmethod
    @transaction.atomic
    def close_for_sibling_transfer(
        *, order_id: int, sibling_order_id: int | None
    ) -> None:
        """Sổ xuất đã chuyển sang đơn xuất lại → đơn cũ hết nợ, đóng dòng theo dõi.

        Gọi từ `StockSalesService.transfer_issue_to_sibling` chứ không phải từ
        từng call-site: sweep `repair_reissue_ledger`, webhook huỷ và luồng phiếu
        hoàn đều chuyển sổ, đặt ở caller là chắc chắn quên một chỗ (prod 20/08:
        16 watch kẹt 'waiting' vì sweep đêm chuyển sổ mà không ai đụng watch).

        Không có watch (đơn chưa từng huỷ-sau-xuất) → no-op. Còn nợ kho (chuyển sổ
        một phần vì đã hoàn tay một ít) → GIỮ 'waiting', sổ vẫn là nguồn sự thật.
        """
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id, status=WatchStatus.WAITING)
            .first()
        )
        if watch is None or order_goods_still_out(order_id):
            return
        watch.status = WatchStatus.CLOSED
        watch.closed_reason = CloseReason.REISSUED
        watch.closed_note = (
            f"Sổ xuất chuyển sang đơn xuất lại #{sibling_order_id}"
            if sibling_order_id is not None
            else "Sổ xuất đã chuyển sang đơn xuất lại"
        )
        watch.closed_by = None
        watch.closed_at = timezone.now()
        watch.save(
            update_fields=[
                "status", "closed_reason", "closed_note",
                "closed_by", "closed_at", "updated_at",
            ]
        )
        logger.info(
            "Đơn %s: sổ xuất chuyển sang đơn %s, đóng dòng theo dõi",
            order_id, sibling_order_id,
        )

    @staticmethod
    @transaction.atomic
    def refresh_for_order(order_id: int) -> None:
        """Đồng bộ watch với sổ sau huỷ/điều chỉnh phiếu hoàn: nợ → WAITING,
        hết nợ → RETURNED. Không có watch (đơn chưa từng huỷ-sau-xuất) → no-op."""
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id)
            .first()
        )
        if watch is None:
            return
        if order_goods_still_out(order_id):
            if watch.status != WatchStatus.WAITING:
                AwaitingReturnService._reopen(watch)
        elif watch.status != WatchStatus.RETURNED and not _is_reissue_closed(watch):
            watch.status = WatchStatus.RETURNED
            watch.save(update_fields=["status", "updated_at"])
            logger.info("Đơn %s: hàng đã về đủ, đóng dòng theo dõi", order_id)

    @staticmethod
    @transaction.atomic
    def settle_if_returned(order_id: int) -> None:
        """Hàng đã về hết (không còn gì ngoài kho) → đóng dòng theo dõi.

        Chạy cả với dòng đã ``closed``: hàng về muộn sau khi ai đó đóng vì tưởng
        mất thì trạng thái phải phản ánh sự thật, không cần nút mở lại.
        """
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id)
            .exclude(status=WatchStatus.RETURNED)
            .first()
        )
        if watch is None or _is_reissue_closed(watch) or order_goods_still_out(order_id):
            return
        watch.status = WatchStatus.RETURNED
        watch.save(update_fields=["status", "updated_at"])
        logger.info("Đơn %s: hàng đã về đủ, đóng dòng theo dõi", order_id)

    @staticmethod
    def reconcile_waiting(*, dry_run: bool = False, limit: int = 0) -> dict:
        """Đối soát lại mọi dòng 'chờ hàng về' với sổ và sửa nhãn cho đúng.

        Watch chỉ được cập nhật bởi các sự kiện đi qua đúng vài luồng; đường nào
        đổi sổ mà không gọi service này thì dòng kẹt 'waiting' im lặng. Lệnh chạy
        lại được, không phải migration — sổ luôn là nguồn sự thật:

        - còn nợ kho          → giữ 'waiting'
        - hết nợ + có bút toán chuyển sổ sang đơn xuất lại → đóng, lý do REISSUED
        - hết nợ, không phải chuyển sổ → 'returned' (hàng đã thật sự về kho)

        Không N+1: dùng chung `_owed_by_order` (2 truy vấn cho cả tập).
        """
        qs = CancelledDispatchWatch.objects.filter(status=WatchStatus.WAITING).order_by(
            "cancelled_at", "id"
        )
        order_ids = list(qs.values_list("order_id", flat=True))
        if limit > 0:
            order_ids = order_ids[:limit]
        owed, _ = AwaitingReturnService._owed_by_order(order_ids)
        transferred = set(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id__in=order_ids,
                movement_type=MovementType.SALE_REVERSAL,
                note=SIBLING_TRANSFER_RELEASE_NOTE,
            ).values_list("reference_id", flat=True)
        )
        summary: dict = {
            "dry_run": dry_run,
            "scanned": len(order_ids),
            "still_waiting": 0,
            "reissued": 0,
            "returned": 0,
            "rows": [],
        }
        for order_id in order_ids:
            if order_id in owed:
                summary["still_waiting"] += 1
                continue
            action = "reissued" if order_id in transferred else "returned"
            summary[action] += 1
            summary["rows"].append({"order_id": order_id, "action": action})
            if dry_run:
                continue
            if action == "reissued":
                AwaitingReturnService.close_for_sibling_transfer(
                    order_id=order_id,
                    sibling_order_id=find_live_reissued_sibling(order_id),
                )
            else:
                AwaitingReturnService.refresh_for_order(order_id)
        return summary

    @staticmethod
    def _owed_by_order(order_ids: list[int]) -> tuple[dict[int, list[tuple[int, int]]], set[int]]:
        """SL còn nợ kho của từng đơn, theo (product_id, qty) — đọc từ sổ.

        Hai truy vấn gộp cho CẢ trang (không N+1): phần đã trừ tồn, trừ đi phần các
        phiếu hoàn đã ghi sổ cộng lại. Cùng công thức `order_goods_still_out`.
        """
        deducted = (
            StockMovement.objects.filter(
                reference_type="order",
                reference_id__in=order_ids,
                movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            )
            .values("reference_id", "product_id")
            .annotate(net=Sum("qty_delta"))
        )
        owed: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for row in deducted:
            if row["net"] < 0:
                owed[row["reference_id"]][row["product_id"]] += -row["net"]

        result: dict[int, list[tuple[int, int]]] = {}
        product_ids: set[int] = set()
        for order_id, by_product in owed.items():
            returned = returned_by_product(order_id)
            lines = [
                (product_id, qty - returned.get(product_id, 0))
                for product_id, qty in by_product.items()
                if qty - returned.get(product_id, 0) > 0
            ]
            if lines:
                result[order_id] = sorted(lines)
                product_ids.update(product_id for product_id, _ in lines)
        return result, product_ids

    @staticmethod
    def list_page(
        *,
        # Rỗng/None = mọi kho; nhiều id = lọc theo tập kho đã chọn.
        warehouse_ids: list[int] | None = None,
        status: str = WatchStatus.WAITING,
        min_days: int = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        """Một trang danh sách. SL còn nợ luôn đọc từ sổ, không lưu trong bảng."""
        from apps.catalog.models import Product

        qs = CancelledDispatchWatch.objects.select_related("order", "warehouse").order_by(
            "cancelled_at", "id"
        )
        if status:
            qs = qs.filter(status=status)
        if warehouse_ids:
            qs = qs.filter(warehouse_id__in=warehouse_ids)
        if min_days > 0:
            qs = qs.filter(cancelled_at__lte=timezone.now() - timedelta(days=min_days))
        total = qs.count()
        watches = list(qs[offset : offset + limit])
        if not watches:
            return total, []

        owed, product_ids = AwaitingReturnService._owed_by_order(
            [watch.order_id for watch in watches]
        )
        skus = dict(Product.objects.filter(id__in=product_ids).values_list("id", "sku"))
        # Ghi chú tự do của dòng — RIÊNG của tab này, không dùng chung với ghi chú
        # dòng cùng đơn ở tab Xuất (docs/flow-history-notes.md §2.3). 1 query/trang.
        notes = FlowNoteService.notes_for(
            StockFlowNoteTarget.AWAITING_RETURN, [watch.order_id for watch in watches]
        )

        now = timezone.now()
        results = []
        for watch in watches:
            results.append(
                {
                    "order_id": watch.order_id,
                    "order_code": watch.order.order_code,
                    "invoice_code": watch.order.invoice_code,
                    "shipping_code": watch.order.shipping_code,
                    "customer_name": watch.order.name,
                    "warehouse_id": watch.warehouse_id,
                    "warehouse_name": watch.warehouse.name,
                    "cancelled_at": watch.cancelled_at,
                    "days_waiting": (now - watch.cancelled_at).days,
                    "status": watch.status,
                    "closed_reason": watch.closed_reason,
                    "closed_note": watch.closed_note,
                    "note": notes.get(watch.order_id, ""),
                    "lines": [
                        {
                            "product_id": product_id,
                            "sku": skus.get(product_id, str(product_id)),
                            "qty": qty,
                        }
                        for product_id, qty in owed.get(watch.order_id, [])
                    ],
                }
            )
        return total, results

    @staticmethod
    @transaction.atomic
    def close(
        *, order_id: int, reason: str, note: str = "", user_id: int | None = None
    ) -> CancelledDispatchWatch:
        """Đóng sổ đơn treo vì hàng không bao giờ về.

        KHÔNG ghi bút toán kho: hàng không quay về nghĩa là tồn hiện tại đã đúng.
        Ghi thêm bút toán ở đây sẽ làm tồn sai đúng bằng lượng hàng đó.
        """
        if reason not in AwaitingReturnService.MANUAL_CLOSE_REASONS:
            raise ValueError("Lý do đóng không hợp lệ.")
        if reason == CloseReason.OTHER and not note.strip():
            raise ValueError("Chọn lý do 'Khác' thì phải ghi chú.")
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id)
            .first()
        )
        if watch is None:
            raise ValueError("Đơn không nằm trong danh sách chờ hàng về.")
        if watch.status == WatchStatus.RETURNED:
            raise ValueError("Hàng của đơn này đã về kho — không cần đóng.")
        watch.status = WatchStatus.CLOSED
        watch.closed_reason = reason
        watch.closed_note = note.strip()
        watch.closed_by_id = user_id
        watch.closed_at = timezone.now()
        watch.save(
            update_fields=[
                "status",
                "closed_reason",
                "closed_note",
                "closed_by",
                "closed_at",
                "updated_at",
            ]
        )
        logger.info("Đóng đơn chờ hàng về %s (lý do %s)", order_id, reason)
        return watch
