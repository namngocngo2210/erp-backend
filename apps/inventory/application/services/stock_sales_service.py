from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.db.models import Max, Q, Sum

from apps.common.events import event_bus
from apps.inventory.application.services.combo_expansion import expand_desired_quantities
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.contracts.stock_events import StockWentNegative
from apps.inventory.models import (
    MovementType,
    ReturnNoteLine,
    ReturnStatus,
    StockMovement,
    StockState,
)

logger = logging.getLogger(__name__)

_REFERENCE_TYPE = "order"
_SALE_TYPES = (MovementType.SALE, MovementType.SALE_REVERSAL)

# Note đánh dấu cặp movement "huỷ đơn → hồi sinh đơn". Reversal do HUỶ đơn (subscriber
# gọi release không kèm note — dữ liệu cũ mang note rỗng) và phần trừ lại khi đơn được
# hồi sinh. Reversal của un-dispatch/resync mang note KHÁC nên không lẫn vào đây.
CANCEL_RELEASE_NOTE = "Hoàn tồn do huỷ đơn"
REVIVE_REISSUE_NOTE = "Trừ lại tồn do đơn huỷ được hồi sinh"
# Note của cặp movement đồng bộ tồn khi items bị sửa SAU xuất kho (resync).
RESYNC_NOTE = "Đồng bộ tồn do sửa items sau xuất kho"
# Cặp note CHUYỂN SỔ khi huỷ đơn mà tồn tại đơn xuất-lại (`<mã>.NN`, cùng mã vận
# đơn) chưa có sổ: hàng vẫn ngoài kho, chỉ dời outstanding từ đơn huỷ sang đơn
# mới — tồn kho không đổi. Xem `transfer_issue_to_sibling`.
SIBLING_TRANSFER_RELEASE_NOTE = "Chuyển sổ xuất sang đơn xuất lại"
SIBLING_TRANSFER_REISSUE_NOTE = "Nhận sổ xuất từ đơn bị huỷ"
# Bút toán thu hồi phần tồn đã auto-hoàn lúc huỷ, ghi khi hàng của đơn huỷ CŨ
# (huỷ trước 2026-07-29) mới về kho. Đường tương thích dữ liệu cũ — từ ngày đó
# huỷ đơn không còn tự hoàn tồn nên không sinh thêm ca này.
AUTO_RELEASE_RECLAIM_NOTE = "Thu hồi tồn đã hoàn tự động khi huỷ"
# Cặp note CHUYỂN SỔ combo → mã lẻ của backfill 0038 (`combo_stock_backfill`):
# trả outstanding về cho SKU combo rồi trừ lại đúng lượng trên từng mã lẻ. Tồn
# TỔNG không đổi, chỉ đổi địa chỉ. Xem docs/combo-stock-expansion-2026-08-20.md.
COMBO_BACKFILL_RELEASE_NOTE = "Chuyển sổ combo sang mã lẻ"
COMBO_BACKFILL_ISSUE_NOTE = "Nhận sổ từ SKU combo"
# SALE HÀNH CHÍNH: bút toán trừ tồn KHÔNG tương ứng một lần hàng rời kho (trừ lại
# khi đơn huỷ được hồi sinh, trừ thêm khi sửa items sau xuất, nhận sổ từ đơn bị
# huỷ). Nguồn sự thật duy nhất cho cả đường ghi (`real_dispatch_occurred_at`) lẫn
# đường đọc (`DispatchHistoryService._dispatch_last_at`) — hai bên lệch nhau là
# ngày xuất lệch theo.
ADMIN_SALE_NOTES = (
    REVIVE_REISSUE_NOTE,
    RESYNC_NOTE,
    SIBLING_TRANSFER_REISSUE_NOTE,
    AUTO_RELEASE_RECLAIM_NOTE,
)
# CỐ Ý KHÔNG có `COMBO_BACKFILL_ISSUE_NOTE` ở đây, khác với ba note trên. Lý do:
# mọi note trong `ADMIN_SALE_NOTES` đều mang mốc thời gian LÚC GHI (hồi sinh đơn,
# sửa items, nhận sổ từ đơn huỷ) nên phải loại khỏi ngày xuất; còn bút toán nhận
# sổ từ combo giữ nguyên `occurred_at` của bút toán GỐC, tức vẫn là lần hàng thật
# rời kho ngày đó, chỉ đổi từ SKU combo sang mã lẻ. Xếp nó vào đây thì mã lẻ biến
# mất khỏi sản lượng xuất trong khi dòng combo vẫn còn — đúng lỗi đã gặp trên
# prod ngày 20/08 sau khi chạy backfill. Xem docs/combo-stock-expansion-2026-08-20.md.
# Lock class riêng cho advisory lock tồn-theo-đơn (2-int form của
# pg_advisory_xact_lock). Mọi thao tác đọc-rồi-ghi movement của 1 đơn
# (issue / release / resync / post-return / cancel-return) phải giữ lock này
# để check-then-insert không bị race giữa 2 request đồng thời.
_ORDER_STOCK_LOCK_CLASS = 61001


def lock_order_stock(order_id: int) -> None:
    """Khóa advisory theo đơn, tự nhả khi transaction kết thúc."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s);",
            [_ORDER_STOCK_LOCK_CLASS, order_id & 0x7FFFFFFF],
        )


def outstanding_by_key(order_id: int) -> dict[tuple[int, int], int]:
    """Số lượng đã trừ tồn nhưng chưa hoàn, theo (warehouse_id, product_id).

    SALE ghi delta âm, SALE_REVERSAL ghi delta dương → outstanding = -SUM(delta).
    Trả về chỉ các key có outstanding ≠ 0.
    """
    rows = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            reference_id=order_id,
            movement_type__in=_SALE_TYPES,
        )
        .values("warehouse_id", "product_id")
        .annotate(net=Sum("qty_delta"))
    )
    return {
        (row["warehouse_id"], row["product_id"]): -row["net"]
        for row in rows
        if row["net"] != 0
    }


def pending_cancel_release_by_key(order_id: int) -> dict[tuple[int, int], int]:
    """Phần tồn đã được auto-hoàn lúc huỷ mà CHƯA được bù, theo (kho, sản phẩm).

    Công thức DUY NHẤT cho cả ba chỗ đọc: điều kiện vào nhánh hồi sinh
    (``subscribers._on_order_status_changed``), ``reissue_after_cancel_release``,
    và bút toán thu hồi khi hàng về (``reclaim_auto_release``). Hai công thức
    song song sẽ lệch nhau và trừ tồn hai lần.

    Reversal do HUỶ = note rỗng (dữ liệu cũ) hoặc ``CANCEL_RELEASE_NOTE``. Phần bù
    = SALE mang ``REVIVE_REISSUE_NOTE`` (hồi sinh) hoặc ``AUTO_RELEASE_RECLAIM_NOTE``
    (hàng về kho). Reversal của un-dispatch / resync mang note khác nên không lẫn vào.
    """
    rows = (
        StockMovement.objects.filter(
            Q(
                movement_type=MovementType.SALE_REVERSAL,
                note__in=("", CANCEL_RELEASE_NOTE),
            )
            | Q(
                movement_type=MovementType.SALE,
                note__in=(REVIVE_REISSUE_NOTE, AUTO_RELEASE_RECLAIM_NOTE),
            ),
            reference_type=_REFERENCE_TYPE,
            reference_id=order_id,
        )
        .values("warehouse_id", "product_id")
        .annotate(net=Sum("qty_delta"))
    )
    return {
        (row["warehouse_id"], row["product_id"]): row["net"]
        for row in rows
        if row["net"] > 0
    }


def has_pending_cancel_release(order_id: int) -> bool:
    return bool(pending_cancel_release_by_key(order_id))


def returned_by_product(order_id: int, *, exclude_entry_id: int | None = None) -> dict[int, int]:
    """Số lượng đã nhập lại tồn qua ReturnEntry ĐÃ ghi sổ của đơn, theo product.

    Tính mọi return_type (kể cả warranty/defect): 1 đơn vị vật lý quay về kho chỉ
    được cộng vào tồn đúng 1 lần, bất kể nhập về state nào.
    """
    qs = ReturnNoteLine.objects.filter(
        entry__source_order_id=order_id,
        entry__status=ReturnStatus.POSTED,
    )
    if exclude_entry_id is not None:
        qs = qs.exclude(entry_id=exclude_entry_id)
    rows = qs.values("product_id").annotate(total=Sum("quantity"))
    return {row["product_id"]: row["total"] for row in rows}


def order_goods_still_out(order_id: int) -> bool:
    """True nếu còn hàng của đơn đang ở NGOÀI kho.

    Khác `order_has_outstanding_stock`: phiếu hoàn ghi `return_in` dưới
    `reference_type="return_entry"` nên KHÔNG làm giảm outstanding của đơn — đọc mỗi
    outstanding sẽ tưởng hàng còn ngoài kho mãi mãi. Công thức đúng là phần đã trừ tồn
    TRỪ phần đã được phiếu hoàn cộng lại, cùng công thức `_guard_double_credit` dùng.
    """
    outstanding: dict[int, int] = defaultdict(int)
    for (_wh, product_id), qty in outstanding_by_key(order_id).items():
        outstanding[product_id] += qty
    returned = returned_by_product(order_id)
    return any(qty - returned.get(product_id, 0) > 0 for product_id, qty in outstanding.items())


def order_has_sales(order_id: int) -> bool:
    return StockMovement.objects.filter(
        reference_type=_REFERENCE_TYPE,
        reference_id=order_id,
        movement_type=MovementType.SALE,
    ).exists()


def order_has_outstanding_stock(order_id: int) -> bool:
    """True nếu đơn còn tồn NET đã trừ chưa hoàn (SALE - SALE_REVERSAL > 0).

    Khác `order_has_sales`: đơn từng SALE nhưng đã bị un-dispatch (SALE_REVERSAL
    trả hết) → net = 0 → False, không còn coi là "đã xuất kho".
    """
    return any(qty > 0 for qty in outstanding_by_key(order_id).values())


def order_ids_with_outstanding_stock(order_ids) -> set[int]:
    """Tập order_id (trong `order_ids`) còn tồn NET đã trừ chưa hoàn.

    Bản batch của `order_has_outstanding_stock`, group theo (order, warehouse,
    product) như `outstanding_by_key` — 1 sản phẩm còn outstanding là đủ để
    đơn được coi là đã xuất kho, kể cả khi sản phẩm khác trong cùng đơn đã
    được hoàn hết.
    """
    rows = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            reference_id__in=order_ids,
            movement_type__in=_SALE_TYPES,
        )
        .values("reference_id", "warehouse_id", "product_id")
        .annotate(net=Sum("qty_delta"))
    )
    return {row["reference_id"] for row in rows if row["net"] < 0}


def real_dispatch_occurred_at(order_id: int) -> datetime | None:
    """Ngày nghiệp vụ của lần hàng THẬT rời kho gần nhất của đơn.

    Dùng khi ghi một bút toán SALE hành chính cho đơn KHÁC (chuyển sổ sang đơn
    xuất lại `<mã>.NN`): đơn nhận không có bút toán thật nào để đường đọc bám
    vào, nên ngày phải được đóng sẵn vào chính bút toán đó.

    Bỏ qua SALE hành chính khi tìm; nhưng nếu đơn CHỈ có SALE hành chính (đơn
    `.01` lại bị huỷ rồi xuất lại `.02`) thì lấy luôn ngày của chúng — nếu không
    mỗi vòng huỷ-xuất lại đẩy ngày đi một nấc. ``None`` khi đơn chưa có bút toán
    SALE nào ⇒ caller để `record_movement` mặc định `now()`.
    """
    row = (
        StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            reference_id=order_id,
            movement_type=MovementType.SALE,
        )
        .aggregate(
            real=Max("occurred_at", filter=~Q(note__in=ADMIN_SALE_NOTES)),
            latest=Max("occurred_at"),
        )
    )
    return row["real"] or row["latest"]


@dataclass(frozen=True)
class SaleLine:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class StockWarning:
    product_id: int
    balance_after: int


class StockSalesService:
    @staticmethod
    @transaction.atomic
    def issue_for_order(
        *,
        order_id: int,
        warehouse_id: int,
        lines: list[SaleLine],
        user_id: int | None = None,
        occurred_at: datetime | None = None,
    ) -> list[StockWarning]:
        """Trừ tồn khi đơn xuất kho. Idempotent theo trạng thái NET: nếu đơn đang
        có tồn đã trừ chưa hoàn thì bỏ qua; nếu đã hoàn hết (huỷ → mở lại) thì
        cho phép trừ lại — không bị "nuốt" như guard exists() cũ.

        ``occurred_at``: thời điểm hàng thực sự rời kho. Luồng khớp mã chờ truyền
        thời điểm QUÉT; mặc định None ⇒ bây giờ.
        """
        lock_order_stock(order_id)
        outstanding = outstanding_by_key(order_id)
        if sum(qty for qty in outstanding.values() if qty > 0) > 0:
            return []

        desired: dict[int, int] = defaultdict(int)
        for line in lines:
            if line.quantity > 0:
                desired[line.product_id] += line.quantity
        desired = expand_desired_quantities(desired)

        warnings: list[StockWarning] = []
        for product_id, quantity in desired.items():
            result = StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-quantity,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
            if result.went_negative:
                warnings.append(StockWarning(product_id, result.balance_after))
            if result.crossed_negative:
                # Alert Lark chỉ khi CHUYỂN sang âm — đang âm bán tiếp không bắn
                # lại (spam giết cảnh báo thật); UI vẫn nhận StockWarning ở trên.
                event_bus.publish_on_commit(
                    StockWentNegative(
                        warehouse_id=warehouse_id,
                        product_id=product_id,
                        balance_after=result.balance_after,
                    )
                )
        return warnings

    @staticmethod
    @transaction.atomic
    def reclaim_auto_release(
        *,
        order_id: int,
        needed_by_product: dict[int, int],
        user_id: int | None = None,
    ) -> None:
        """Thu hồi phần tồn đã auto-hoàn lúc huỷ, đúng bằng lượng hàng vừa về kho.

        CHỈ chạm dữ liệu CŨ: đơn huỷ trước 2026-07-29 còn mang reversal do huỷ, tồn
        đã được cộng từ lúc đó nên phiếu hoàn không được cộng thêm lần nữa. Đơn huỷ
        theo luật mới không có phần dư nào → hàm này là no-op.

        Ghi SALE (âm) chứ không sửa bút toán cũ: sổ append-only. ``needed_by_product``
        bị TRỪ tại chỗ theo phần đã thu hồi để caller biết phần còn lại.
        """
        lock_order_stock(order_id)
        for (warehouse_id, product_id), available in sorted(
            pending_cancel_release_by_key(order_id).items()
        ):
            needed = needed_by_product.get(product_id, 0)
            if needed <= 0:
                continue
            qty = min(needed, available)
            needed_by_product[product_id] = needed - qty
            logger.info(
                "Thu hồi tồn đã auto-hoàn đơn %s: kho=%s sp=%s sl=%s",
                order_id, warehouse_id, product_id, qty,
            )
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-qty,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=AUTO_RELEASE_RECLAIM_NOTE,
                user_id=user_id,
            )

    @staticmethod
    @transaction.atomic
    def release_for_order(
        *, order_id: int, user_id: int | None = None, note: str = ""
    ) -> None:
        """Hoàn tồn phần còn outstanding của đơn (huỷ đơn / un-dispatch).

        Trừ đi phần đã nhập lại qua ReturnNote đã ghi sổ — tránh cộng tồn 2 lần
        khi hàng hoàn đã được quét nhập trước rồi đơn mới bị huỷ. Idempotent:
        gọi lại khi outstanding = 0 là no-op."""
        lock_order_stock(order_id)
        outstanding = outstanding_by_key(order_id)
        if not outstanding:
            return

        returned = returned_by_product(order_id)
        credit_used: dict[int, int] = defaultdict(int)
        for (warehouse_id, product_id), qty in sorted(outstanding.items()):
            if qty <= 0:
                continue
            remaining_credit = max(0, returned.get(product_id, 0) - credit_used[product_id])
            credited = min(qty, remaining_credit)
            credit_used[product_id] += credited
            restore = qty - credited
            if restore <= 0:
                continue
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=restore,
                movement_type=MovementType.SALE_REVERSAL,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=note,
                user_id=user_id,
            )

    @staticmethod
    @transaction.atomic
    def transfer_issue_to_sibling(
        *, order_id: int, sibling_order_id: int, user_id: int | None = None
    ) -> bool:
        """Chuyển outstanding của đơn bị huỷ sang đơn xuất-lại chưa có sổ.

        KiotViet huỷ-xuất lại hoá đơn nhưng bản ``.NN`` về trước / về qua cron
        import → thành DÒNG ĐƠN MỚI thay vì reattach. Nếu cứ hoàn tồn cho đơn
        cũ thì không ai trừ lại (đơn "hồi sinh" là bản ghi khác) → tồn phồng.
        Thay vào đó ghi cặp bút toán cùng lúc: reversal trên đơn cũ + sale trên
        đơn mới, từng (kho, sản phẩm) — tổng tồn không đổi.

        Phần đã nhập lại qua ReturnNote ghi sổ được giữ nguyên trên đơn cũ
        (không chuyển) — cùng luật credit với ``release_for_order``.

        Trả ``False`` (không đụng gì) nếu đơn mới đã có sổ SALE/SALE_REVERSAL
        riêng — khi đó caller hoàn tồn như bình thường. Idempotent: gọi lại khi
        outstanding đơn cũ = 0 là no-op trả ``False``.
        """
        for oid in sorted((order_id, sibling_order_id)):
            lock_order_stock(oid)
        if StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE,
            reference_id=sibling_order_id,
            movement_type__in=_SALE_TYPES,
        ).exists():
            return False

        outstanding = outstanding_by_key(order_id)
        returned = returned_by_product(order_id)
        # Hàng đã rời kho từ lần xuất của đơn CŨ — chuyển sổ chỉ đổi đơn đứng tên,
        # không phải một lần xuất mới. Bút toán reversal vẫn mang `now()`: đảo sổ
        # là nghiệp vụ xảy ra tại thời điểm huỷ (docs/superpowers/specs/
        # 2026-07-22-scan-match-business-date-design.md §7).
        dispatched_at = real_dispatch_occurred_at(order_id)
        credit_used: dict[int, int] = defaultdict(int)
        moved = False
        for (warehouse_id, product_id), qty in sorted(outstanding.items()):
            if qty <= 0:
                continue
            remaining_credit = max(0, returned.get(product_id, 0) - credit_used[product_id])
            credited = min(qty, remaining_credit)
            credit_used[product_id] += credited
            move = qty - credited
            if move <= 0:
                continue
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=move,
                movement_type=MovementType.SALE_REVERSAL,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=SIBLING_TRANSFER_RELEASE_NOTE,
                user_id=user_id,
            )
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-move,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=sibling_order_id,
                note=SIBLING_TRANSFER_REISSUE_NOTE,
                user_id=user_id,
                occurred_at=dispatched_at,
            )
            moved = True
        if moved:
            logger.info(
                "Chuyển sổ xuất đơn %s → đơn xuất lại %s", order_id, sibling_order_id
            )
            # Đơn cũ vừa hết nợ kho → gỡ khỏi hàng đợi "chờ hàng về". Đặt Ở ĐÂY chứ
            # không ở 3 call-site (subscriber huỷ, sweep repair_reissue_ledger,
            # luồng phiếu hoàn): quên một chỗ là watch kẹt 'waiting' vĩnh viễn và
            # kho đi đòi hàng không tồn tại (prod 20/08: 16 dòng rác, đều do sweep
            # đêm). Import cục bộ vì awaiting_return_service import ngược module này.
            from apps.inventory.application.services.awaiting_return_service import (
                AwaitingReturnService,
            )

            AwaitingReturnService.close_for_sibling_transfer(
                order_id=order_id, sibling_order_id=sibling_order_id
            )
        return moved

    @staticmethod
    @transaction.atomic
    def reissue_after_cancel_release(
        *, order_id: int, user_id: int | None = None
    ) -> list[StockWarning]:
        """Đơn HUỶ được hồi sinh → trừ lại ĐÚNG phần tồn mà lần huỷ đã cộng trả.

        Đọc thẳng từ sổ movement thay vì suy từ trạng thái đơn: cộng mọi SALE_REVERSAL
        do HUỶ (note rỗng = release từ subscriber huỷ, hoặc `CANCEL_RELEASE_NOTE`) trừ
        đi phần đã trừ lại ở các lần hồi sinh trước (`REVIVE_REISSUE_NOTE`). Phần dư > 0
        chính là lượng tồn đang phồng → ghi SALE bù đúng từng ấy, tại đúng kho cũ.

        Vì sao KHÔNG dùng `issue_for_order`: nó no-op khi outstanding > 0, mà đơn trả
        hàng MỘT PHẦN sau khi huỷ vẫn còn outstanding → phần chưa trả không bao giờ được
        trừ lại. Vì sao KHÔNG lọc theo trạng thái giao: đơn đã xuất kho còn được Sale
        chuyển sang `received`/`not_received`/… — không mã nào hoàn tồn, nên lọc theo mã
        sẽ bỏ sót đúng ca phổ biến nhất (audit 2026-07-14 vòng 2).

        Reversal của un-dispatch / resync mang note riêng nên KHÔNG bị tính — đơn chưa
        rời kho thì hồi sinh không trừ gì cả. Idempotent: gọi lại lần 2 thì phần dư = 0.
        """
        lock_order_stock(order_id)
        warnings: list[StockWarning] = []
        for (warehouse_id, product_id), pending in sorted(
            pending_cancel_release_by_key(order_id).items()
        ):
            # Đây là bút toán TỰ ĐỘNG trừ tồn, chạy trong on_commit handler (lỗi bị bus
            # nuốt) → phải có vết để đối soát khi số tồn bị nghi ngờ.
            logger.info(
                "Trừ lại tồn đơn %s: kho=%s sp=%s sl=%s",
                order_id, warehouse_id, product_id, pending,
            )
            result = StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-pending,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=REVIVE_REISSUE_NOTE,
                user_id=user_id,
            )
            if result.went_negative:
                warnings.append(StockWarning(product_id, result.balance_after))
            if result.crossed_negative:
                # Alert Lark chỉ khi CHUYỂN sang âm — đang âm bán tiếp không bắn
                # lại (spam giết cảnh báo thật); UI vẫn nhận StockWarning ở trên.
                event_bus.publish_on_commit(
                    StockWentNegative(
                        warehouse_id=warehouse_id,
                        product_id=product_id,
                        balance_after=result.balance_after,
                    )
                )
        return warnings

    @staticmethod
    @transaction.atomic
    def resync_for_order(
        *, order_id: int, warehouse_id: int, lines: list[SaleLine], user_id: int | None = None
    ) -> list[StockWarning]:
        """Đồng bộ lại tồn đã trừ theo items mới khi đơn ĐÃ xuất kho bị sửa items.

        Sản phẩm giảm/bỏ → hoàn phần chênh (tôn trọng credit ReturnNote như
        release); sản phẩm tăng/thêm → trừ thêm tại kho xuất hiện tại. Đơn chưa
        xuất (outstanding = 0) là no-op."""
        lock_order_stock(order_id)
        outstanding = outstanding_by_key(order_id)
        deducted_by_product: dict[int, int] = defaultdict(int)
        for (_wh, product_id), qty in outstanding.items():
            deducted_by_product[product_id] += qty
        if sum(qty for qty in deducted_by_product.values() if qty > 0) <= 0:
            return []

        desired: dict[int, int] = defaultdict(int)
        for line in lines:
            if line.quantity > 0:
                desired[line.product_id] += line.quantity
        # Nở TRƯỚC khi so với `deducted_by_product` — sổ đã ghi theo mã lẻ, so
        # thẳng SKU combo sẽ thấy "combo thừa, mã lẻ thiếu" rồi vừa hoàn vừa trừ.
        desired = expand_desired_quantities(desired)

        returned = returned_by_product(order_id)
        warnings: list[StockWarning] = []
        for product_id in sorted(set(desired) | set(deducted_by_product)):
            target = desired.get(product_id, 0)
            current = deducted_by_product.get(product_id, 0)
            if current > target:
                # Phần đã có ReturnNote cộng lại thì không hoàn nữa.
                reducible = max(0, current - max(target, returned.get(product_id, 0)))
                for (wh_id, prod_id), qty in sorted(outstanding.items()):
                    if prod_id != product_id or qty <= 0 or reducible <= 0:
                        continue
                    take = min(qty, reducible)
                    StockCommandService.record_movement(
                        warehouse_id=wh_id,
                        product_id=product_id,
                        qty_delta=take,
                        movement_type=MovementType.SALE_REVERSAL,
                        state=StockState.SELLABLE,
                        reference_type=_REFERENCE_TYPE,
                        reference_id=order_id,
                        note=RESYNC_NOTE,
                        user_id=user_id,
                    )
                    reducible -= take
            elif target > current:
                result = StockCommandService.record_movement(
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    qty_delta=-(target - current),
                    movement_type=MovementType.SALE,
                    state=StockState.SELLABLE,
                    reference_type=_REFERENCE_TYPE,
                    reference_id=order_id,
                    note=RESYNC_NOTE,
                    user_id=user_id,
                )
                if result.went_negative:
                    warnings.append(StockWarning(product_id, result.balance_after))
                if result.crossed_negative:
                    event_bus.publish_on_commit(
                        StockWentNegative(
                            warehouse_id=warehouse_id,
                            product_id=product_id,
                            balance_after=result.balance_after,
                        )
                    )
        return warnings
