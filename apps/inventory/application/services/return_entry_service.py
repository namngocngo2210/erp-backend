from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.inventory.application.services.return_note_service import (
    ReturnNoteService,
    lock_day,
)
from apps.inventory.application.services.return_order_sync import (
    mark_order_returned,
    revert_order_status,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    StockSalesService,
    lock_order_stock,
    order_has_sales,
    outstanding_by_key,
    pending_cancel_release_by_key,
    returned_by_product,
)
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnNoteLine,
    ReturnNoteStatus,
    ReturnStatus,
    ReturnType,
    StockMovement,
    StockState,
)

logger = logging.getLogger(__name__)

RETURN_ENTRY_REFERENCE = "return_entry"

# Advisory lock class riêng cho mã vận đơn khi tạo nhóm đơn hoàn. Constraint
# `returnentry_shipcode_draft_uniq` chỉ phủ status=draft, mà luồng quét
# tạo-rồi-ghi-sổ trong 1 transaction nên tại commit dòng đã POSTED — 2 request
# song song cùng mã (không có đơn nguồn nên không đi qua lock_order_stock)
# đều lọt cả check app-level lẫn unique index → RETURN_IN ghi 2 lần. Lock này
# serialize mọi đường tạo entry cùng shipping_code; request sau thấy row đã
# commit của request trước → check trùng chặn như luồng tuần tự.
_SHIPCODE_LOCK_CLASS = 61002


def lock_shipping_code(shipping_code: str) -> None:
    """Khoá advisory theo mã vận đơn, tự nhả khi transaction kết thúc."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, hashtext(%s) & 2147483647);",
            [_SHIPCODE_LOCK_CLASS, shipping_code],
        )
_RETURN_STATE = {
    ReturnType.DELIVERY_RETURNED: StockState.SELLABLE,
    ReturnType.EXTERNAL_RETURN: StockState.SELLABLE,
    ReturnType.WARRANTY: StockState.WARRANTY,
    ReturnType.DEFECT: StockState.DEFECT,
}


def build_lines(entry: ReturnEntry, lines: list[dict]) -> list[ReturnNoteLine]:
    """Dựng ReturnNoteLine từ payload; loại hoàn là BẮT BUỘC.

    Không mặc định về delivery_returned: hàng bảo hành/lỗi mà thiếu loại sẽ âm
    thầm nhập vào tồn bán được — sai bucket, chỉ phát hiện được khi kiểm kê.
    """
    from apps.catalog.models import Product  # local import: giữ ranh giới module

    product_ids = [line["product_id"] for line in lines]
    skus = dict(
        Product.objects.filter(id__in=product_ids).values_list("id", "sku")
    )
    built: list[ReturnNoteLine] = []
    seen: set[tuple[int, str]] = set()
    for line in lines:
        return_type = (line.get("return_type") or "").strip()
        if not return_type:
            product_id = line["product_id"]
            label = skus.get(product_id) or f"#{product_id}"
            raise ValueError(f"Dòng {label}: thiếu loại hoàn.")
        # Chặn sớm thay vì để `uniq_return_line` bắn IntegrityError 500. Quan
        # trọng hơn: 2 dòng cùng SKU mỗi dòng ĐỀU lọt guard hạn mức riêng lẻ —
        # constraint DB đang là chốt chặn over-credit duy nhất cho ca này.
        key = (line["product_id"], return_type)
        if key in seen:
            label = skus.get(line["product_id"]) or f"#{line['product_id']}"
            raise ValueError(
                f"Dòng {label}: trùng sản phẩm/loại hoàn trong phiếu — gộp số lượng lại."
            )
        seen.add(key)
        built.append(
            ReturnNoteLine(
                entry=entry,
                product_id=line["product_id"],
                quantity=line["quantity"],
                return_type=return_type,
                condition_note=line.get("condition_note", ""),
            )
        )
    return built


class ReturnEntryService:
    """Nhóm đơn hoàn (ReturnEntry) — đơn vị ghi sổ/huỷ thật sự trong phiếu ngày."""

    @staticmethod
    def _never_dispatched_order_code(order_id: int) -> str | None:
        """Mã đơn nguồn nếu đơn đó đang HUỶ, ngược lại None.

        Dùng cho ca đơn huỷ mà CHƯA từng xuất kho: hàng chưa bao giờ rời kho nên phiếu
        hoàn sẽ tạo tồn ảo. Tách riêng để báo cho nhân viên kho bằng câu dễ hiểu thay vì
        thông điệp kỹ thuật "đơn nguồn chỉ còn 0 đơn vị…".
        """
        from apps.orders.models import Order, OrderStatus  # local import: giữ ranh giới module

        row = (
            Order.objects.filter(pk=order_id)
            .values("order_code", "status", "invoice_status")
            .first()
        )
        if row is None:
            return None
        cancelled = int(row["status"]) == int(OrderStatus.CANCELLED) or row["invoice_status"] == 2
        return (row["order_code"] or f"#{order_id}") if cancelled else None

    @staticmethod
    def _guard_double_credit(
        entry: ReturnEntry, lines: list[ReturnNoteLine]
    ) -> None:
        """Chặn ghi sổ khi các đơn vị này đã được cộng lại tồn cho đơn nguồn rồi
        (đã có nhóm đơn khác ghi sổ), hoặc khi hàng chưa từng rời kho.

        Constraint DB `returnentry_shipcode_draft_uniq` chỉ phủ nhóm đơn ở trạng thái
        DRAFT (chặn trùng shipping_code khi còn nháp) — `create_and_post_entry` (luồng
        quét khớp mã) tạo rồi ghi sổ NGAY trong 1 transaction, nhóm đơn rời khỏi DRAFT
        gần như tức thì nên constraint đó không chặn được 2 transaction cùng
        create_and_post cho cùng đơn nguồn chạy song song. Guard này (đọc outstanding
        dưới khoá advisory `lock_order_stock`) mới là tầng chặn THẬT của trường hợp đó
        — đừng gỡ vì tưởng constraint đã đủ.
        """
        if not entry.source_order_id:
            return
        # Đơn nguồn có thể là sibling `.NN` của một đơn huỷ còn outstanding (webhook
        # huỷ đến trước khi sibling tồn tại → sổ chưa được chuyển). Resolve parent
        # TRƯỚC khi khoá để khoá được cả cặp theo thứ tự toàn cục — cùng protocol
        # sorted-advisory với `transfer_issue_to_sibling` (tránh AB/BA deadlock).
        from apps.inventory.application.services.reissue_sibling import (
            find_cancelled_source_order,
        )

        parent_id = find_cancelled_source_order(entry.source_order_id)
        lock_ids = {entry.source_order_id}
        if parent_id is not None:
            lock_ids.add(parent_id)
        for oid in sorted(lock_ids):
            lock_order_stock(oid)
        if not order_has_sales(entry.source_order_id):
            # Sibling chưa có sổ nhưng parent huỷ còn nợ outstanding → hàng NÀY chính
            # là hàng đã rời kho dưới tên parent. Chuyển sổ ngay tại chỗ rồi đi tiếp
            # check hạn mức như thường — KHÔNG fail-open, nếu không outstanding của
            # parent còn nguyên và 1 kiện hàng có thể được cộng tồn 2 lần.
            transferred = parent_id is not None and StockSalesService.transfer_issue_to_sibling(
                order_id=parent_id, sibling_order_id=entry.source_order_id
            )
            if not transferred:
                # Đơn HUỶ chưa từng xuất kho: hàng chưa bao giờ rời kho → cộng tồn ở
                # đây là tồn ẢO. Chặn (chỉ với đơn huỷ; đơn cũ chưa từng trừ tồn qua
                # module kho vẫn fail-open như trước — không có gì để đối chiếu).
                cancelled_code = ReturnEntryService._never_dispatched_order_code(
                    entry.source_order_id
                )
                if cancelled_code:
                    raise ValueError(
                        f"Đơn {cancelled_code} đã hủy khi chưa xuất kho — hàng chưa bao giờ "
                        "rời kho nên không có gì để nhập hoàn."
                    )
                return
        deducted: dict[int, int] = defaultdict(int)
        for (_wh, product_id), qty in outstanding_by_key(entry.source_order_id).items():
            deducted[product_id] += qty
        # Đơn huỷ CŨ (trước 2026-07-29) đã được auto-hoàn tồn: outstanding = 0 nhưng hàng
        # vẫn từng rời kho và nay mới về. Tính vào hạn mức để phiếu hoàn được nhận;
        # `post_entry` ghi bút toán thu hồi nên tồn không cộng hai lần (spec §6).
        for (_wh, product_id), qty in pending_cancel_release_by_key(
            entry.source_order_id
        ).items():
            deducted[product_id] += qty
        already_returned = returned_by_product(
            entry.source_order_id, exclude_entry_id=entry.id
        )
        for line in lines:
            available = deducted.get(line.product_id, 0) - already_returned.get(
                line.product_id, 0
            )
            if line.quantity > available:
                raise ValueError(
                    f"Sản phẩm #{line.product_id}: đơn nguồn chỉ còn {max(available, 0)} "
                    f"đơn vị chưa được cộng lại tồn (phiếu nhập {line.quantity}). "
                    "Đơn đã có phiếu trả khác ghi sổ, hoặc hàng đã về kho trước đó."
                )

    @staticmethod
    @transaction.atomic
    def create_entry(
        *,
        warehouse_id: int,
        lines: list[dict],
        doc_date: date | None = None,
        source_order_id: int | None = None,
        shipping_code: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        source_note: str = "",
        note: str = "",
        user_id: int | None = None,
        scan_batch_id: str | None = None,
    ) -> ReturnEntry:
        if shipping_code:
            # Serialize mọi đường tạo entry cùng mã TRƯỚC khi check trùng — xem
            # comment tại `lock_shipping_code`.
            lock_shipping_code(shipping_code)
            # Invariant: 1 mã vận đơn ↔ 1 lần hoàn chưa huỷ (chống khớp hoàn 2 lần
            # → cộng tồn gấp đôi). Nhóm đơn ĐÃ HUỶ không tính: cho phép quét lại.
            dup = (
                ReturnEntry.objects.filter(shipping_code=shipping_code)
                .exclude(status=ReturnStatus.CANCELLED)
                .select_related("return_note")
                .order_by("-id")
                .first()
            )
            if dup is not None:
                # Nháp 0 dòng (di sản luồng quét cũ) không mang bút toán nào và giờ
                # KHÔNG còn cửa nào ghi sổ/khớp đơn — nếu vẫn để nó giữ mã thì mã đó
                # bị khoá chết: quét lại luôn bị chặn, hàng hoàn không bao giờ được
                # cộng tồn. Tự huỷ nháp rỗng rồi cho quét tiếp (audit 14/07 vòng 3).
                if dup.status == ReturnStatus.DRAFT and not dup.lines.exists():
                    logger.info(
                        "Tự huỷ nhóm đơn nháp rỗng #%s (phiếu %s) đang giữ mã %s",
                        dup.id, dup.return_note.code, shipping_code,
                    )
                    dup.status = ReturnStatus.CANCELLED
                    dup.save(update_fields=["status"])
                else:
                    raise ValueError(
                        f"Mã {shipping_code} đã có trong phiếu {dup.return_note.code} "
                        "(chưa huỷ) — không tạo trùng."
                    )
        note_obj = ReturnNoteService.resolve_open_note(
            warehouse_id=warehouse_id,
            doc_date=doc_date or ReturnNoteService.today_vn(),
            user_id=user_id,
        )
        try:
            entry = ReturnEntry.objects.create(
                return_note=note_obj,
                source_order_id=source_order_id,
                shipping_code=shipping_code,
                customer_name=customer_name,
                customer_phone=customer_phone,
                source_note=source_note,
                note=note,
                created_by_id=user_id,
                scan_batch_id=scan_batch_id,
            )
        except IntegrityError as exc:
            if "returnentry_shipcode_draft_uniq" in str(exc):
                raise ValueError(
                    f"Mã {shipping_code} đã được ghi nhận ở phiếu trả khác."
                ) from exc
            raise
        ReturnNoteLine.objects.bulk_create(build_lines(entry, lines))
        return entry

    @staticmethod
    @transaction.atomic
    def post_entry(
        *, entry_id: int, user_id: int | None = None, occurred_at=None
    ) -> ReturnEntry:
        # Khoá NGÀY trước, khoá dòng sau — cùng thứ tự với resolve_open_note /
        # close_note / merge_return_notes_by_day → không sinh chu trình deadlock.
        # Không có khoá ngày thì "Chốt phiếu" và ghi sổ chạy song song: phiếu vừa
        # chốt xong vẫn nhận thêm bút toán (sổ đã ký lại đổi entry_count/qty_total).
        # Đọc doc_date/kho trước khi khoá (rẻ, không khoá): entry chỉ có thể được
        # chuyển sang phiếu khác CÙNG (ngày, kho) (xem re-home bên dưới) nên khoá
        # ngày đọc trước vẫn đúng khoá cần giữ.
        head = ReturnEntry.objects.values_list(
            "return_note__doc_date", "return_note__warehouse_id"
        ).get(id=entry_id)
        lock_day(head[0], head[1])
        # of=("self",) → chỉ khoá dòng ReturnEntry; không khoá lây dòng
        # stock_return_notes qua select_related (deadlock với cancel_note).
        # Đọc lại SAU khi có khoá ngày: phiếu của entry có thể vừa bị chốt.
        entry = (
            ReturnEntry.objects.select_for_update(of=("self",))
            .select_related("return_note")
            .get(id=entry_id)
        )
        if entry.status != ReturnStatus.DRAFT:
            raise ValueError(f"Chỉ nhóm đơn nháp mới ghi sổ được (đang {entry.status}).")
        note_obj = entry.return_note
        if note_obj.status == ReturnNoteStatus.CLOSED:
            # Phiếu ngày đã chốt = sổ đã khoá, nội dung sổ của nó là các nhóm đơn ĐÃ
            # ghi sổ. Nháp (mã quét chưa khớp, hôm sau mới khớp được đơn) chưa từng
            # ghi sổ gì → chuyển sang phiếu ĐANG MỞ CÙNG (ngày, kho) rồi ghi sổ ở đó,
            # thay vì mắc kẹt vĩnh viễn trong phiếu đã chốt.
            # resolve_open_note lấy lại đúng khoá ngày ta đang giữ — pg_advisory_xact_lock
            # re-entrant trong cùng transaction (đếm tham chiếu, nhả hết khi commit).
            note_obj = ReturnNoteService.resolve_open_note(
                warehouse_id=note_obj.warehouse_id,
                doc_date=note_obj.doc_date,
                user_id=user_id,
            )
            entry.return_note = note_obj
            entry.save(update_fields=["return_note"])
        lines = list(entry.lines.select_related("product"))
        if not lines:
            raise ValueError("Chưa có dòng hàng — hãy khớp đơn trước khi ghi sổ.")

        # Khoá Order row TRƯỚC advisory 61001 (lấy bên trong _guard_double_credit qua
        # lock_order_stock) — cùng thứ tự với luồng dispatch (update_order_inline khoá
        # Order.select_for_update() rồi mới vào issue_for_order lấy advisory 61001).
        # Đảo ngược thứ tự ở 1 trong 2 luồng tạo chu trình chờ chéo → deadlock khi
        # dispatch và post-return chạy đúng lúc trên cùng đơn. Khoá cả với entry
        # warranty/defect (không chỉ delivery_returned) để mọi truy cập advisory của
        # đơn này đều đi sau Order row lock, không riêng nhánh mark_order_returned.
        order = None
        if entry.source_order_id:
            from apps.orders.models import Order  # local import: giữ ranh giới module

            order = (
                Order.objects.select_for_update()
                .filter(pk=entry.source_order_id)
                .first()
            )

        ReturnEntryService._guard_double_credit(entry, lines)
        if entry.source_order_id:
            # Dữ liệu CŨ: tồn đã được auto-hoàn lúc huỷ → thu hồi đúng phần hàng vừa về
            # để `return_in` bên dưới không cộng lần thứ hai. No-op với đơn huỷ mới.
            needed: dict[int, int] = defaultdict(int)
            for line in lines:
                needed[line.product_id] += line.quantity
            StockSalesService.reclaim_auto_release(
                order_id=entry.source_order_id,
                needed_by_product=needed,
                user_id=user_id,
            )
        for line in lines:
            StockCommandService.record_movement(
                warehouse_id=note_obj.warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.RETURN_IN,
                state=_RETURN_STATE[line.return_type],
                reference_type=RETURN_ENTRY_REFERENCE,
                reference_id=entry.id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
        entry.status = ReturnStatus.POSTED
        entry.posted_at = timezone.now()
        update_fields = ["status", "posted_at"]

        has_delivery_returned = any(
            line.return_type == ReturnType.DELIVERY_RETURNED for line in lines
        )
        if has_delivery_returned and order is not None:
            previous = mark_order_returned(order, user_id=user_id)
            if previous is not None:
                entry.order_status_before = previous
                update_fields.append("order_status_before")

        entry.save(update_fields=update_fields)
        if entry.source_order_id:
            # Hàng về đủ thì dòng theo dõi "đơn huỷ chờ hàng về" tự đóng — không ai
            # phải bấm gì. Import cục bộ: awaiting_return_service đọc ngược service này.
            from apps.inventory.application.services.awaiting_return_service import (
                AwaitingReturnService,
            )

            AwaitingReturnService.settle_if_returned(entry.source_order_id)
        return entry

    @staticmethod
    @transaction.atomic
    def create_and_post_entry(
        *, user_id: int | None = None, occurred_at=None, **create_kwargs
    ) -> ReturnEntry:
        """Tạo + ghi sổ trong 1 transaction (luồng quét khớp mã).

        Lỗi ở create hoặc post → rollback toàn bộ: không nhóm đơn, không dòng,
        không movement, không đổi status đơn.

        ``occurred_at`` chỉ đi vào bút toán kho; ngày chứng từ của phiếu vẫn do
        ``doc_date`` trong ``create_kwargs`` quyết định (hai thứ khác nhau: phiếu
        gộp theo NGÀY, bút toán giữ cả GIỜ).
        """
        entry = ReturnEntryService.create_entry(user_id=user_id, **create_kwargs)
        return ReturnEntryService.post_entry(
            entry_id=entry.id, user_id=user_id, occurred_at=occurred_at
        )

    @staticmethod
    @transaction.atomic
    def cancel_entry(*, entry_id: int, user_id: int | None = None) -> ReturnEntry:
        # of=("self",) → chỉ khoá dòng ReturnEntry (xem post_entry).
        entry = (
            ReturnEntry.objects.select_for_update(of=("self",))
            .select_related("return_note")
            .get(id=entry_id)
        )
        # Nháp cũng huỷ được (chỉ set CANCELLED — không có movement để đảo): sau khi UI
        # phiếu trả bị gỡ, nháp không còn cửa nào ghi sổ, mà guard chống-trùng vẫn giữ
        # mã vận đơn của nó → phải có đường dọn. Đã huỷ rồi thì thôi.
        if entry.status == ReturnStatus.CANCELLED:
            raise ValueError("Nhóm đơn đã huỷ.")
        # Khoá Order row TRƯỚC advisory 61001 — cùng thứ tự với dispatch/post_entry
        # (xem comment trong post_entry), tránh chu trình chờ chéo (deadlock).
        order = None
        if entry.source_order_id:
            from apps.orders.models import Order  # local import: giữ ranh giới module

            order = (
                Order.objects.select_for_update()
                .filter(pk=entry.source_order_id)
                .first()
            )
            # Serialize với release/resync của đơn nguồn để credit không bị đọc lệch.
            lock_order_stock(entry.source_order_id)
        movements = StockMovement.objects.filter(
            reference_type=RETURN_ENTRY_REFERENCE, reference_id=entry.id
        )
        for mv in movements:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id,
                product_id=mv.product_id,
                qty_delta=-mv.qty_delta,
                movement_type=mv.movement_type,
                state=mv.state,
                reference_type=RETURN_ENTRY_REFERENCE,
                reference_id=entry.id,
                note=f"Huỷ nhóm đơn #{entry.id} trong phiếu {entry.return_note.code}",
                user_id=user_id,
            )
        entry.status = ReturnStatus.CANCELLED
        update_fields = ["status"]

        if order is not None and entry.order_status_before is not None:
            from apps.orders.models import OrderStatus  # local import: giữ ranh giới module

            if int(order.status) == int(OrderStatus.RETURNED):
                revert_order_status(order, entry.order_status_before, user_id=user_id)
                entry.order_status_before = None
                update_fields.append("order_status_before")

        entry.save(update_fields=update_fields)
        if entry.shipping_code:
            # Huỷ lần nhập hoàn = thu hồi luôn DÒNG QUÉT đã sinh ra nó. Thiếu bước
            # này thì dòng kẹt ở `matched` nên vẫn hiện trên tab Nhập hoàn, lại mượn
            # SKU × SL của nhóm đơn còn sống cùng mã (`_attach_return_notes`) → một
            # mã bị bắn 3 lần hiện 3 dòng, nhìn như đã nhập 3 lần trong khi kho chỉ
            # nhận 1. Đồng thời nhả mã ra cho lần bắn sau.
            # Import cục bộ: pending_scan_service đọc ngược service này.
            from apps.inventory.application.services.pending_scan_service import (
                void_return_scans,
            )

            voided = void_return_scans(code=entry.shipping_code, user_id=user_id)
            if voided:
                logger.info(
                    "Huỷ nhóm đơn #%s → thu hồi %s dòng quét nhập hoàn của mã %s",
                    entry.id, voided, entry.shipping_code,
                )
        if entry.source_order_id:
            # Bút toán đảo có thể đưa hàng trở lại trạng thái "nợ kho" — watch
            # chờ hàng về phải mở lại theo sổ, không giữ nhãn RETURNED cũ.
            from apps.inventory.application.services.awaiting_return_service import (
                AwaitingReturnService,
            )

            AwaitingReturnService.refresh_for_order(entry.source_order_id)
        return entry

    @staticmethod
    def update_note(*, entry_id: int, note: str) -> ReturnEntry:
        """Sửa ghi chú nhóm đơn ở mọi trạng thái — xem return_entry_edit_service."""
        from apps.inventory.application.services.return_entry_edit_service import (
            update_note,
        )

        return update_note(entry_id=entry_id, note=note)

    @staticmethod
    def adjust_entry(
        *, entry_id: int, lines: list[dict], user_id: int | None = None
    ) -> ReturnEntry:
        """Điều chỉnh số lượng dòng của nhóm đơn ĐÃ GHI SỔ — xem
        return_entry_adjust_service."""
        from apps.inventory.application.services.return_entry_adjust_service import (
            adjust_entry,
        )

        return adjust_entry(entry_id=entry_id, lines=lines, user_id=user_id)
