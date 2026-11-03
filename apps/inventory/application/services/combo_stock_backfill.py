"""Chuyển sổ phần tồn đã trừ nhầm vào SKU combo sang các mã lẻ.

Trước 2026-08-20 CRM trừ tồn vào chính SKU combo — mã không bao giờ được nhập kho
— trong khi KiotViet trừ vào mã lẻ. Hệ quả: tồn mã lẻ cao hơn thực tế, tồn SKU
combo âm vĩnh viễn. Xem docs/combo-stock-expansion-2026-08-20.md.

Tách khỏi file migration để CÓ THỂ TEST được, cùng cách làm với
``occurred_at_backfill`` — migration chỉ gọi vào đây.

CHỈ CHUYỂN PHẦN OUTSTANDING (net = SALE − SALE_REVERSAL > 0), không đụng từng bút
toán lịch sử. Lý do: tồn hiện tại chỉ phụ thuộc net, còn đơn đã hoàn hết thì tồn
SKU combo đã về 0 và mã lẻ chưa từng bị trừ — không có gì để sửa. Sổ là append-only
nên chuyển sổ bằng bút toán mới, không UPDATE bút toán cũ.

Idempotent: chạy lại là no-op vì lượt trước đã đưa outstanding của SKU combo về 0.

Chỉ xử lý combo ĐÃ KHAI thành phần tại thời điểm chạy. Combo chưa khai không có
công thức nào để chia — giữ nguyên như cũ, khai xong chạy lại là được.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from django.db import transaction
from django.db.models import Max, Sum

from apps.inventory.application.services.combo_expansion import all_combo_components
from apps.inventory.application.services.stock_command_service import StockCommandService
# Note nằm ở `stock_sales_service` cùng các note ghi sổ khác: `COMBO_BACKFILL_ISSUE_NOTE`
# PHẢI có mặt trong `ADMIN_SALE_NOTES` (bút toán HÀNH CHÍNH, không phải một lần hàng
# thật rời kho) — để chung một chỗ thì không ai thêm note mới mà quên đăng ký.
from apps.inventory.application.services.stock_sales_service import (
    COMBO_BACKFILL_ISSUE_NOTE,
    COMBO_BACKFILL_RELEASE_NOTE,
    returned_by_product,
)
from apps.inventory.models import MovementType, StockMovement, StockState

logger = logging.getLogger(__name__)

_REFERENCE_TYPE = "order"


def backfill_combo_stock(*, dry_run: bool = False) -> dict:
    """Chuyển outstanding của SKU combo sang mã lẻ. Trả về số liệu kiểm toán."""
    components = all_combo_components()
    if not components:
        logger.info("Backfill combo: chưa có combo nào khai thành phần — bỏ qua.")
        return {"combos": 0, "orders": 0, "moved": 0, "skipped_returned": 0, "lines": []}

    rows = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            product_id__in=components.keys(),
        )
        .values("reference_id", "warehouse_id", "product_id")
        .annotate(net=Sum("qty_delta"), last_at=Max("occurred_at"))
    )
    # net < 0 nghĩa là còn hàng đã trừ chưa hoàn (SALE ghi qty_delta âm).
    pending = [r for r in rows if r["net"] < 0]
    if not pending:
        logger.info("Backfill combo: không còn outstanding nào trên SKU combo.")
        return {"combos": 0, "orders": 0, "moved": 0, "skipped_returned": 0, "lines": []}

    audit: list[dict] = []
    orders: set[int] = set()
    combos: set[int] = set()
    moved = 0
    skipped_returned = 0
    # Credit phiếu hoàn dùng CHUNG trong một đơn, y hệt `release_for_order`: một đơn
    # có thể có nhiều kho / nhiều dòng cho cùng SKU combo.
    credit_used: dict[tuple[int, int], int] = defaultdict(int)
    returned_cache: dict[int, dict[int, int]] = {}
    for row in sorted(
        pending, key=lambda r: (r["reference_id"], r["warehouse_id"], r["product_id"])
    ):
        outstanding = -row["net"]
        combo_id = row["product_id"]
        order_id = row["reference_id"]
        # Hàng ĐÃ hoàn về kho thì không còn ở ngoài để mà chuyển sổ. Trên prod có
        # đúng ca này: đơn 548336 bán combo ở kho 1, hàng hoàn về nhập kho 2 dưới
        # CHÍNH SKU combo (phiếu hoàn ghi `reference_type='return_entry'` nên không
        # làm giảm outstanding của đơn — xem `order_goods_still_out`). Chuyển sổ phần
        # đó sẽ vừa trừ mã lẻ vừa để nguyên 1 combo trong kho = tạo tồn ảo.
        # Cố ý KHÔNG tự động quy đổi 1 combo trong kho thành các mã lẻ: đó là hàng
        # vật lý, phải đi qua phiếu chuyển đổi/kiểm kê của kho chứ không phải một
        # migration âm thầm.
        if order_id not in returned_cache:
            returned_cache[order_id] = returned_by_product(order_id)
        credited = min(
            outstanding,
            max(0, returned_cache[order_id].get(combo_id, 0) - credit_used[(order_id, combo_id)]),
        )
        if credited:
            credit_used[(order_id, combo_id)] += credited
            outstanding -= credited
            skipped_returned += credited
            logger.warning(
                "Backfill combo: đơn=%s kho=%s combo=%s BỎ QUA %s đơn vị đã hoàn về kho "
                "dưới chính SKU combo — cần phiếu chuyển đổi tay nếu muốn tách ra mã lẻ.",
                order_id, row["warehouse_id"], combo_id, credited,
            )
        if outstanding <= 0:
            continue
        orders.add(order_id)
        combos.add(combo_id)
        moved += outstanding
        audit.append(
            {
                "order_id": row["reference_id"],
                "warehouse_id": row["warehouse_id"],
                "combo_product_id": combo_id,
                "quantity": outstanding,
                "components": [
                    {"product_id": cid, "quantity": outstanding * per}
                    for cid, per in components[combo_id]
                ],
            }
        )
        logger.info(
            "Backfill combo: đơn=%s kho=%s combo=%s sl=%s → %s",
            row["reference_id"],
            row["warehouse_id"],
            combo_id,
            outstanding,
            [(cid, outstanding * per) for cid, per in components[combo_id]],
        )
        if dry_run:
            continue

        with transaction.atomic():
            # Ngày nghiệp vụ giữ theo bút toán GỐC, không phải ngày chạy migration —
            # nếu không sản lượng xuất của kỳ trước sẽ nhảy sang kỳ hiện tại.
            occurred_at = row["last_at"]
            StockCommandService.record_movement(
                warehouse_id=row["warehouse_id"],
                product_id=combo_id,
                qty_delta=outstanding,
                movement_type=MovementType.SALE_REVERSAL,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=row["reference_id"],
                note=COMBO_BACKFILL_RELEASE_NOTE,
                occurred_at=occurred_at,
            )
            for component_id, per_combo in components[combo_id]:
                StockCommandService.record_movement(
                    warehouse_id=row["warehouse_id"],
                    product_id=component_id,
                    qty_delta=-(outstanding * per_combo),
                    movement_type=MovementType.SALE,
                    state=StockState.SELLABLE,
                    reference_type=_REFERENCE_TYPE,
                    reference_id=row["reference_id"],
                    note=COMBO_BACKFILL_ISSUE_NOTE,
                    occurred_at=occurred_at,
                )

    summary = {
        "combos": len(combos),
        "orders": len(orders),
        "moved": moved,
        "skipped_returned": skipped_returned,
        "lines": audit,
    }
    logger.info(
        "Backfill combo %s: %s combo · %s đơn · %s đơn vị chuyển sổ, %s đơn vị bỏ qua "
        "vì đã hoàn về kho.",
        "(dry-run)" if dry_run else "hoàn tất",
        summary["combos"],
        summary["orders"],
        summary["moved"],
        summary["skipped_returned"],
    )
    return summary


def combo_outstanding_report() -> list[dict]:
    """Ảnh chụp phần hàng CÒN NGOÀI KHO đang ghi trên SKU combo — để đối chiếu
    trước/sau backfill.

    Dùng ĐÚNG công thức của `backfill_combo_stock` (outstanding trừ phần đã hoàn
    về kho): nếu không, ca hàng đã hoàn về mà backfill cố ý bỏ qua sẽ luôn hiện ra
    như "còn sót" sau khi chạy, khiến log kiểm toán báo động giả mãi mãi.
    """
    components = all_combo_components()
    if not components:
        return []
    rows = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            product_id__in=components.keys(),
        )
        .values("reference_id", "product_id")
        .annotate(net=Sum("qty_delta"))
    )
    totals: dict[int, int] = defaultdict(int)
    for row in rows:
        if row["net"] >= 0:
            continue
        outstanding = -row["net"]
        credited = min(
            outstanding, returned_by_product(row["reference_id"]).get(row["product_id"], 0)
        )
        remaining = outstanding - credited
        if remaining > 0:
            totals[row["product_id"]] += remaining
    return [
        {"product_id": product_id, "outstanding": qty}
        for product_id, qty in sorted(totals.items())
    ]
