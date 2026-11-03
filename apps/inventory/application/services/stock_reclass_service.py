from __future__ import annotations

from django.db import transaction

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockItem, StockReclassLog, StockState

_REFERENCE_TYPE = "reclass"
_VALID_ACTIONS = {"to_sellable", "send_back", "scrap"}
_RESOLVABLE_STATES = {StockState.WARRANTY, StockState.DEFECT}


class StockReclassService:
    @staticmethod
    @transaction.atomic
    def reclassify(
        *, warehouse_id: int, product_id: int, quantity: int, from_state: str,
        action: str, note: str = "", user_id: int | None = None,
    ) -> StockReclassLog:
        if quantity <= 0:
            raise ValueError("quantity phải > 0.")
        if action not in _VALID_ACTIONS:
            raise ValueError(f"action không hợp lệ: {action}.")
        if from_state not in _RESOLVABLE_STATES:
            raise ValueError(f"Chỉ xử lý tồn warranty/defect (nhận {from_state}).")

        # Khóa row tồn nguồn rồi kiểm tra đủ số lượng — khác luồng bán (cho phép
        # âm có cảnh báo), xử lý warranty/defect vượt tồn luôn là thao tác sai.
        available = (
            StockItem.objects.select_for_update()
            .filter(warehouse_id=warehouse_id, product_id=product_id, state=from_state)
            .values_list("quantity", flat=True)
            .first()
            or 0
        )
        if quantity > available:
            raise ValueError(
                f"Không đủ tồn {from_state}: còn {available}, yêu cầu {quantity}."
            )

        log = StockReclassLog.objects.create(
            warehouse_id=warehouse_id,
            product_id=product_id,
            from_state=from_state,
            action=action,
            quantity=quantity,
            note=note,
            created_by_id=user_id,
        )
        ref = dict(reference_type=_REFERENCE_TYPE, reference_id=log.id, user_id=user_id)
        if action == "to_sellable":
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=-quantity,
                movement_type=MovementType.RECLASS_OUT, state=from_state, note=note, **ref)
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=quantity,
                movement_type=MovementType.RECLASS_IN, state=StockState.SELLABLE, note=note, **ref)
        else:  # send_back | scrap → xuất khỏi tồn (disposal)
            reason = note or ("Gửi lại khách" if action == "send_back" else "Huỷ hàng lỗi")
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=-quantity,
                movement_type=MovementType.DISPOSAL, state=from_state, note=reason, **ref)
        return log
