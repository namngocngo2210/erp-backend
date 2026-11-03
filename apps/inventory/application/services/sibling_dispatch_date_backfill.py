"""Nắn ngày nghiệp vụ cho sổ xuất ĐÃ chuyển sang đơn xuất lại `<mã>.NN`.

Trước khi `transfer_issue_to_sibling` / `ReissueLedgerRepairService.repair` biết
truyền `occurred_at`, bút toán "Nhận sổ xuất từ đơn bị huỷ" mang thời điểm
CHUYỂN SỔ. Đơn `.NN` không có bút toán xuất thật nào khác, nên tab Xuất lấy
đúng dòng đó làm ngày xuất → đơn nhảy sang ngày huỷ/tạo đơn mới (ca prod
HD013469 → HD013469.01). Sửa đường ghi chỉ đúng cho dòng mới; đây là lượt nắn
một lần cho dòng cũ.

Là management command chứ không phải data migration: tập dòng nhỏ (đếm bằng
chục), cần chạy `--dry-run` cho vận hành duyệt trước, và cùng kiểu thao tác với
`repair_reissue_ledger` — hai lệnh cùng chữa một lớp sự cố thì nên chạy giống
nhau.

Chỉ nắn LÙI (`new < occurred_at`) nên bất biến `occurred_at <= created_at` được
giữ miễn phí, và chạy lại là no-op. `occurred_at` không phải partition key nên
UPDATE không làm dòng nhảy partition.
"""
from __future__ import annotations

import logging

from django.db import transaction

from apps.inventory.application.services.reissue_sibling import (
    find_cancelled_source_order,
)
from apps.inventory.application.services.stock_sales_service import (
    SIBLING_TRANSFER_REISSUE_NOTE,
    real_dispatch_occurred_at,
)
from apps.inventory.models import MovementType, StockMovement

logger = logging.getLogger(__name__)

_REFERENCE_TYPE = "order"


def _transferred_movements(limit: int) -> list[dict]:
    """Bút toán nhận sổ, theo id TĂNG DẦN.

    Thứ tự id chính là thứ tự thời gian ghi sổ, nên chuỗi `X` → `X.01` → `X.02`
    được nắn từ mắt xích sớm nhất: mắt xích sau đọc lại giá trị vừa nắn của mắt
    xích trước thay vì kế thừa ngày sai.
    """
    qs = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            movement_type=MovementType.SALE,
            note=SIBLING_TRANSFER_REISSUE_NOTE,
        )
        .order_by("id")
        .values("id", "created_at", "occurred_at", "reference_id")
    )
    return list(qs[:limit] if limit else qs)


def backfill_sibling_dispatch_dates(
    *, dry_run: bool = False, limit: int = 0
) -> dict:
    """Trả summary cho command: số dòng quét / nắn / bỏ qua và chi tiết từng dòng."""
    rows: list[dict] = []
    skipped = 0
    movements = _transferred_movements(limit)
    for movement in movements:
        source_id = find_cancelled_source_order(movement["reference_id"])
        dispatched_at = (
            real_dispatch_occurred_at(source_id) if source_id is not None else None
        )
        if dispatched_at is None or dispatched_at >= movement["occurred_at"]:
            skipped += 1
            continue
        rows.append(
            {
                "movement_id": movement["id"],
                "order_id": movement["reference_id"],
                "source_order_id": source_id,
                "occurred_at": movement["occurred_at"].isoformat(),
                "new_occurred_at": dispatched_at.isoformat(),
            }
        )
        if dry_run:
            continue
        with transaction.atomic():
            # Kẹp thêm `created_at` (partition key) để UPDATE chỉ chạm đúng một
            # partition thay vì quét cả bảng sổ.
            StockMovement.objects.filter(
                pk=movement["id"], created_at=movement["created_at"]
            ).update(occurred_at=dispatched_at)
        logger.info(
            "Nắn ngày xuất đơn %s: %s → %s (nguồn: đơn huỷ %s)",
            movement["reference_id"],
            movement["occurred_at"],
            dispatched_at,
            source_id,
        )
    return {
        "dry_run": dry_run,
        "scanned": len(movements),
        "updated": len(rows),
        "skipped": skipped,
        "rows": rows,
    }
