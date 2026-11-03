from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    StockDocument,
    StockDocumentLine,
    StockItem,
    StockMovement,
    StockState,
)

_CODE_PREFIX = {
    DocType.RECEIPT: "NK",
    DocType.ISSUE: "XK",
    DocType.ADJUSTMENT: "DC",
    DocType.TRANSFER: "CK",
}
_REFERENCE_TYPE = "document"


class StockDocumentService:
    @staticmethod
    def _generate_code(doc_type: str, bump: int = 0) -> str:
        prefix = _CODE_PREFIX.get(doc_type, "PK")
        today = timezone.now().strftime("%Y%m%d")
        same_day = StockDocument.objects.filter(
            code__startswith=f"{prefix}{today}"
        ).count()
        return f"{prefix}{today}-{same_day + 1 + bump:04d}"

    @staticmethod
    @transaction.atomic
    def create_document(
        *,
        doc_type: str,
        lines: list[dict],
        source_warehouse_id: int | None = None,
        dest_warehouse_id: int | None = None,
        note: str = "",
        user_id: int | None = None,
        created_at=None,
        occurred_at=None,
    ) -> StockDocument:
        # Ngày người dùng nhập trên form phiếu ("Ngày chứng từ", lưu ở `created_at`)
        # CHÍNH LÀ ngày nghiệp vụ: không nối vào đây thì phiếu ghi lùi ngày sẽ tạo
        # bút toán mang ngày DUYỆT và biến mất khỏi khung lọc trang Lịch sử nhập
        # xuất (vốn lọc theo `occurred_at`). Caller truyền thẳng `occurred_at`
        # (luồng phiếu tự sinh từ mã quét) vẫn được ưu tiên — mốc quét sát nghiệp
        # vụ hơn ngày lập phiếu.
        occurred_at = occurred_at if occurred_at is not None else created_at
        if occurred_at is not None and occurred_at > timezone.now():
            # `record_movement` sẽ chặn occurred_at tương lai lúc DUYỆT — nhưng
            # khi đó phiếu đã tạo xong và kẹt không duyệt được. Chặn ngay từ đầu.
            raise ValueError("Ngày chứng từ (occurred_at) không được ở tương lai.")
        create_kwargs = {
            "doc_type": doc_type,
            "source_warehouse_id": source_warehouse_id,
            "dest_warehouse_id": dest_warehouse_id,
            "note": note,
            "created_by_id": user_id,
            "occurred_at": occurred_at,
        }
        if created_at is not None:
            create_kwargs["created_at"] = created_at
        doc = None
        for attempt in range(5):
            try:
                with transaction.atomic():
                    doc = StockDocument.objects.create(
                        code=StockDocumentService._generate_code(doc_type, bump=attempt),
                        **create_kwargs,
                    )
                break
            except IntegrityError:
                # Trùng code do 2 phiếu tạo đồng thời cùng ngày → thử mã kế tiếp.
                continue
        if doc is None:
            raise ValueError("Không sinh được mã phiếu, vui lòng thử lại.")
        StockDocumentLine.objects.bulk_create(
            [
                StockDocumentLine(
                    document=doc,
                    product_id=line["product_id"],
                    quantity=line["quantity"],
                )
                for line in lines
            ]
        )
        return doc

    @staticmethod
    @transaction.atomic
    def update_document(
        *,
        document_id: int,
        doc_type: str,
        lines: list[dict],
        source_warehouse_id: int | None = None,
        dest_warehouse_id: int | None = None,
        note: str = "",
        created_at=None,
    ) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới sửa được (đang {doc.status}).")
        doc.doc_type = doc_type
        doc.source_warehouse_id = source_warehouse_id
        doc.dest_warehouse_id = dest_warehouse_id
        doc.note = note
        update_fields = ["doc_type", "source_warehouse", "dest_warehouse", "note"]
        if created_at is not None:
            if created_at > timezone.now():
                raise ValueError("Ngày chứng từ (occurred_at) không được ở tương lai.")
            doc.created_at = created_at
            # Sửa ngày chứng từ phải kéo theo ngày nghiệp vụ, nếu không phiếu nháp
            # sửa ngày rồi duyệt vẫn rơi vào mốc cũ (xem `create_document`).
            doc.occurred_at = created_at
            update_fields += ["created_at", "occurred_at"]
        doc.save(update_fields=update_fields)
        doc.lines.all().delete()
        StockDocumentLine.objects.bulk_create(
            [
                StockDocumentLine(
                    document=doc,
                    product_id=line["product_id"],
                    quantity=line["quantity"],
                )
                for line in lines
            ]
        )
        return doc

    @staticmethod
    @transaction.atomic
    def submit_for_approval(
        *, document_id: int, user_id: int | None = None
    ) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới gửi duyệt được (đang {doc.status}).")
        doc.status = DocStatus.PENDING_APPROVAL
        doc.submitted_by_id = user_id
        doc.submitted_at = timezone.now()
        # Lần gửi duyệt mới → xoá lý do từ chối của vòng trước.
        doc.rejected_reason = ""
        doc.save(update_fields=["status", "submitted_by", "submitted_at", "rejected_reason"])
        return doc

    @staticmethod
    @transaction.atomic
    def approve_document(
        *, document_id: int, user_id: int | None = None
    ) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.PENDING_APPROVAL:
            raise ValueError(f"Chỉ phiếu chờ duyệt mới ghi sổ được (đang {doc.status}).")
        # Khóa các row stock_items theo THỨ TỰ TOÀN CỤC (warehouse_id, product_id),
        # không theo thứ tự dòng nhập: hai phiếu chồng nhau duyệt đồng thời mà
        # khóa theo thứ tự khác nhau là cửa sổ deadlock. Riêng TRANSFER có 2 kho
        # trong 1 phiếu — khoá src→dst theo từng dòng là AB/BA với phiếu ngược
        # chiều (W1→W2 vs W2→W1 cùng sản phẩm) → phải trải phẳng cặp bút toán
        # của mọi dòng rồi sort toàn cục, cùng luật với `cancel_document`. Các
        # doc_type còn lại chỉ có 1 kho nên sort theo product_id là đủ. Chỉ đổi
        # thứ tự VÒNG KHÓA/ghi sổ — thứ tự dòng trên phiếu không đổi.
        lines = list(doc.lines.select_related("product").all())
        if doc.doc_type == DocType.TRANSFER:
            ops = []
            for line in lines:
                ops.append(
                    (doc.source_warehouse_id, line.product_id,
                     -line.quantity, MovementType.TRANSFER_OUT)
                )
                ops.append(
                    (doc.dest_warehouse_id, line.product_id,
                     line.quantity, MovementType.TRANSFER_IN)
                )
            for warehouse_id, product_id, qty_delta, movement_type in sorted(
                ops, key=lambda op: (op[0], op[1])
            ):
                StockCommandService.record_movement(
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    qty_delta=qty_delta,
                    movement_type=movement_type,
                    reference_type=_REFERENCE_TYPE,
                    reference_id=doc.id,
                    user_id=user_id,
                    occurred_at=doc.occurred_at,
                )
        else:
            for line in sorted(lines, key=lambda item: item.product_id):
                StockDocumentService._apply_line(doc, line, user_id)
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.approved_by_id = user_id
        doc.approved_at = timezone.now()
        doc.save(update_fields=["status", "posted_at", "approved_by", "approved_at"])
        return doc

    @staticmethod
    @transaction.atomic
    def reject_document(
        *, document_id: int, user_id: int | None = None, reason: str = ""
    ) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.PENDING_APPROVAL:
            raise ValueError(
                f"Chỉ phiếu chờ duyệt mới từ chối được (đang {doc.status})."
            )
        doc.status = DocStatus.DRAFT
        doc.rejected_reason = reason or ""
        doc.submitted_by = None
        doc.submitted_at = None
        doc.save(
            update_fields=["status", "rejected_reason", "submitted_by", "submitted_at"]
        )
        return doc

    @staticmethod
    def _apply_line(
        doc: StockDocument, line: StockDocumentLine, user_id: int | None
    ) -> None:
        # occurred_at của phiếu (None với phiếu tay) → record_movement tự lấy now().
        # Nhờ đi qua `ref`, cả 4 nhánh doc_type dùng chung một luật, không lệch nhau.
        ref = dict(
            reference_type=_REFERENCE_TYPE,
            reference_id=doc.id,
            user_id=user_id,
            occurred_at=doc.occurred_at,
        )
        if doc.doc_type == DocType.RECEIPT:
            StockCommandService.record_movement(
                warehouse_id=doc.dest_warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.RECEIPT,
                **ref,
            )
        elif doc.doc_type == DocType.ISSUE:
            StockCommandService.record_movement(
                warehouse_id=doc.source_warehouse_id,
                product_id=line.product_id,
                qty_delta=-line.quantity,
                movement_type=MovementType.ISSUE,
                **ref,
            )
        elif doc.doc_type == DocType.ADJUSTMENT:
            # Đảm bảo row tồn tại rồi khóa FOR UPDATE trước khi đọc — nếu không,
            # 1 movement khác chen giữa lúc đọc `current` và lúc ghi delta sẽ làm
            # tồn cuối ≠ số kiểm kê trên phiếu.
            StockItem.objects.get_or_create(
                warehouse_id=doc.dest_warehouse_id,
                product_id=line.product_id,
                state=StockState.SELLABLE,
                defaults={"quantity": 0},
            )
            current = (
                StockItem.objects.select_for_update()
                .filter(
                    warehouse_id=doc.dest_warehouse_id,
                    product_id=line.product_id,
                    state=StockState.SELLABLE,
                )
                .values_list("quantity", flat=True)
                .first()
                or 0
            )
            delta = line.quantity - current
            if delta != 0:
                StockCommandService.record_movement(
                    warehouse_id=doc.dest_warehouse_id,
                    product_id=line.product_id,
                    qty_delta=delta,
                    movement_type=MovementType.ADJUSTMENT,
                    **ref,
                )
        elif doc.doc_type == DocType.TRANSFER:
            StockCommandService.record_movement(
                warehouse_id=doc.source_warehouse_id,
                product_id=line.product_id,
                qty_delta=-line.quantity,
                movement_type=MovementType.TRANSFER_OUT,
                **ref,
            )
            StockCommandService.record_movement(
                warehouse_id=doc.dest_warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.TRANSFER_IN,
                **ref,
            )
        else:
            raise ValueError(f"doc_type {doc.doc_type} không hỗ trợ.")

    @staticmethod
    @transaction.atomic
    def cancel_document(
        *, document_id: int, user_id: int | None = None
    ) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.POSTED:
            raise ValueError(f"Chỉ phiếu đã ghi sổ mới huỷ được (đang {doc.status}).")

        # Cùng luật khóa với approve_document: đảo bút toán theo thứ tự toàn cục
        # (warehouse_id, product_id) để hai phiếu chồng nhau không deadlock.
        movements = sorted(
            StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE, reference_id=doc.id
            ),
            key=lambda item: (item.warehouse_id, item.product_id),
        )
        for mv in movements:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id,
                product_id=mv.product_id,
                qty_delta=-mv.qty_delta,
                movement_type=mv.movement_type,
                state=mv.state,
                reference_type=_REFERENCE_TYPE,
                reference_id=doc.id,
                note=f"Huỷ phiếu {doc.code}",
                user_id=user_id,
            )

        doc.status = DocStatus.CANCELLED
        doc.save(update_fields=["status"])
        return doc
