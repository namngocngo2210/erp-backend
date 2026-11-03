from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.utils import timezone

from apps.inventory.models import StockMovement, StockState


@dataclass(frozen=True)
class MovementResult:
    movement_id: int
    balance_after: int
    # Số dư SAU movement đang âm — nuôi cảnh báo UI (StockWarning) mỗi lần bán.
    went_negative: bool
    # CHUYỂN từ không-âm sang âm ngay tại movement này — nuôi alert Lark.
    # Tách khỏi went_negative: đang âm mà bán tiếp thì UI vẫn cảnh báo, nhưng
    # mỗi lần bán một tin Lark mới là spam giết cảnh báo thật.
    crossed_negative: bool


class StockCommandService:
    """Low-level ledger primitive. All stock changes (documents, sales) go
    through `record_movement`, which appends an immutable movement and updates
    the `StockItem` balance atomically."""

    @staticmethod
    @transaction.atomic
    def record_movement(
        *,
        warehouse_id: int,
        product_id: int,
        qty_delta: int,
        movement_type: str,
        state: str = StockState.SELLABLE,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str = "",
        user_id: int | None = None,
        occurred_at: datetime | None = None,
    ) -> MovementResult:
        """``occurred_at``: thời điểm nghiệp vụ (hàng thực sự rời/về kho).

        Mặc định ``now()`` ⇒ mọi caller cũ không đổi hành vi. Luồng khớp mã chờ
        truyền thời điểm QUÉT để số liệu rơi vào đúng ngày kho làm việc, thay vì
        ngày cron khớp được đơn.
        """
        if qty_delta == 0:
            raise ValueError("qty_delta must be non-zero.")
        now = timezone.now()
        when = occurred_at or now
        if when > now:
            # Bất biến occurred_at <= created_at là cơ sở cho tối ưu partition
            # pruning ở `filter_occurred_between` — phá nó là làm sai báo cáo,
            # không chỉ sai một dòng.
            raise ValueError("occurred_at không được ở tương lai.")

        # Atomic upsert: 1 statement, lock ngầm trên đúng 1 row (warehouse, product, state).
        # RETURNING trả số dư sau cộng dồn, bỏ được race của get_or_create + select_for_update.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO stock_items
                    (warehouse_id, product_id, state, quantity, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (warehouse_id, product_id, state)
                DO UPDATE SET quantity = stock_items.quantity + EXCLUDED.quantity,
                              updated_at = now()
                RETURNING quantity;
                """,
                [warehouse_id, product_id, state, qty_delta],
            )
            new_balance = cursor.fetchone()[0]

        movement = StockMovement.objects.create(
            warehouse_id=warehouse_id,
            product_id=product_id,
            qty_delta=qty_delta,
            movement_type=movement_type,
            state=state,
            balance_after=new_balance,
            reference_type=reference_type,
            reference_id=reference_id,
            note=note,
            created_by_id=user_id,
            occurred_at=when,
        )
        return MovementResult(
            movement_id=movement.id,
            balance_after=new_balance,
            went_negative=new_balance < 0,
            crossed_negative=new_balance < 0 <= new_balance - qty_delta,
        )
