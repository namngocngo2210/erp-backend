"""Điều chỉnh SỐ LƯỢNG dòng hàng của nhóm đơn hoàn ĐÃ GHI SỔ.

Ca dùng: quét hoàn đã ghi sổ full đơn nhưng thực nhận thiếu (đơn 2 SP chỉ về 1)
→ kế toán/kho hạ số lượng tại Lịch sử nhập xuất, thay vì Huỷ + quét lại.

Bút toán: ghi movement RETURN_IN với delta (mới − cũ) trên CÙNG reference của
nhóm đơn — sổ movement tự cân, không sửa đè bút toán cũ (giữ audit trail, cùng
triết lý với cancel_entry ghi bút toán đảo).
"""
from __future__ import annotations

from django.db import transaction

from apps.inventory.application.services.return_note_service import lock_day
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import MovementType, ReturnEntry, ReturnNoteLine, ReturnStatus


@transaction.atomic
def adjust_entry(
    *, entry_id: int, lines: list[dict], user_id: int | None = None
) -> ReturnEntry:
    """Đổi số lượng các dòng gửi lên; quantity 0 = gỡ dòng khỏi phiếu.

    Dòng không gửi lên giữ nguyên. Không thêm được sản phẩm mới (điều chỉnh số
    thực nhận, không phải sửa nội dung phiếu). Hạ MỌI dòng về 0 bị chặn — dùng
    Huỷ nhóm đơn (còn hoàn tác trạng thái đơn nguồn). Tăng số lượng đi qua
    guard chống hoàn vượt phần đã trừ tồn của đơn nguồn như lúc ghi sổ.
    """
    from apps.inventory.application.services.return_entry_service import (
        RETURN_ENTRY_REFERENCE,
        _RETURN_STATE,
        ReturnEntryService,
    )

    # Khoá NGÀY trước, khoá dòng entry sau — cùng thứ tự với post_entry /
    # cancel_entry / resolve_open_note (không sinh chu trình deadlock).
    head = ReturnEntry.objects.values_list(
        "return_note__doc_date", "return_note__warehouse_id"
    ).get(id=entry_id)
    lock_day(head[0], head[1])
    entry = (
        ReturnEntry.objects.select_for_update(of=("self",))
        .select_related("return_note")
        .get(id=entry_id)
    )
    if entry.status != ReturnStatus.POSTED:
        raise ValueError("Chỉ điều chỉnh được nhóm đơn đã ghi sổ.")

    current = {line.product_id: line for line in entry.lines.select_related("product")}
    desired: dict[int, int] = {}
    for line in lines:
        product_id = int(line["product_id"])
        quantity = int(line["quantity"])
        cur = current.get(product_id)
        if cur is None:
            label = f"#{product_id}"
            raise ValueError(f"Sản phẩm {label} không có trong phiếu.")
        if quantity < 0:
            raise ValueError(f"Sản phẩm {cur.product.sku}: số lượng không được âm.")
        desired[product_id] = quantity

    new_qty = {pid: desired.get(pid, line.quantity) for pid, line in current.items()}
    if all(qty == 0 for qty in new_qty.values()):
        raise ValueError(
            "Mọi dòng đều về 0 — dùng Huỷ nhóm đơn thay vì điều chỉnh."
        )

    # Khoá Order row TRƯỚC advisory 61001 (guard lấy qua lock_order_stock) —
    # cùng thứ tự với dispatch/post_entry/cancel_entry, tránh deadlock chéo.
    if entry.source_order_id:
        from apps.orders.models import Order  # local import: giữ ranh giới module

        Order.objects.select_for_update().filter(pk=entry.source_order_id).first()

    guard_lines = [
        ReturnNoteLine(
            entry=entry,
            product_id=pid,
            quantity=qty,
            return_type=current[pid].return_type,
        )
        for pid, qty in new_qty.items()
        if qty > 0
    ]
    ReturnEntryService._guard_double_credit(entry, guard_lines)

    for pid, cur in current.items():
        delta = new_qty[pid] - cur.quantity
        if delta == 0:
            continue
        StockCommandService.record_movement(
            warehouse_id=entry.return_note.warehouse_id,
            product_id=pid,
            qty_delta=delta,
            movement_type=MovementType.RETURN_IN,
            state=_RETURN_STATE[cur.return_type],
            reference_type=RETURN_ENTRY_REFERENCE,
            reference_id=entry.id,
            note=f"Điều chỉnh nhóm đơn #{entry.id} trong phiếu {entry.return_note.code}",
            user_id=user_id,
        )
        if new_qty[pid] == 0:
            cur.delete()
        else:
            cur.quantity = new_qty[pid]
            cur.save(update_fields=["quantity"])

    if entry.source_order_id:
        # Điều chỉnh đổi phần "nợ kho" của đơn nguồn → watch chờ hàng về phải
        # đi theo sổ (hoàn đủ qua điều chỉnh → RETURNED; hạ xuống → WAITING).
        from apps.inventory.application.services.awaiting_return_service import (
            AwaitingReturnService,
        )

        AwaitingReturnService.refresh_for_order(entry.source_order_id)

    return entry
