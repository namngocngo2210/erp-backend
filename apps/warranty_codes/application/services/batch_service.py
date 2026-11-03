"""Nghiệp vụ lô mã bảo hành: cấp dải (nối tiếp hoặc theo khoảng số), sinh serial + mã ngẫu nhiên (CODE_LENGTH ký tự), gán SP, tách lô."""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.warranty_codes.application.services.serial_format import format_serial
from apps.warranty_codes.domain.serial import generate_code
from apps.warranty_codes.models import (
    WarrantyCode,
    WarrantyCodeBatch,
    WarrantyCodeBatchStatus,
    WarrantyCodeCounter,
)

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 5000
_MAX_CODE_RETRY = 3


class WarrantyCodeBatchService:
    @staticmethod
    def create_batch(
        *, quantity: int, product_id: int | None, created_by, start_seq: int | None = None
    ) -> WarrantyCodeBatch:
        """Cấp dải `[start_seq, start_seq + quantity - 1]` rồi sinh mã.

        `start_seq` bỏ trống → nối tiếp counter toàn cục như trước. Có nhập →
        đúng khoảng số người dùng muốn (cho phép nhảy cóc, chỉ chặn đè lên lô
        đã có). Không còn trần số lượng: tới `WARRANTY_CODE_SYNC_GENERATE_MAX`
        sinh ngay trong request, lớn hơn đẩy task nền (xem generate_codes).
        """
        if not isinstance(quantity, int) or quantity < 1:
            raise ValueError("Số lượng phải từ 1 trở lên.")
        if start_seq is not None and (not isinstance(start_seq, int) or start_seq < 1):
            raise ValueError("Số bắt đầu phải từ 1 trở lên.")
        product = None
        if product_id is not None:
            from apps.catalog.models import Product

            product = Product.objects.filter(id=product_id, deleted_at__isnull=True).first()
            if product is None:
                raise ValueError("Không tìm thấy sản phẩm.")
        with transaction.atomic():
            counter = WarrantyCodeCounter.objects.select_for_update().get()
            first = counter.last_seq + 1 if start_seq is None else start_seq
            last = first + quantity - 1
            # Mọi mã đều thuộc một lô có dải bao trùm (tách lô giữ bất biến này),
            # nên chỉ cần so dải với bảng lô; unique(seq) là chốt chặn cuối.
            clash = (
                WarrantyCodeBatch.objects.filter(start_seq__lte=last, end_seq__gte=first)
                .order_by("start_seq")
                .first()
            )
            if clash is not None:
                raise ValueError(
                    f"Dải {first}–{last} trùng lô {clash.batch_code} "
                    f"({format_serial(clash.start_seq)} – {format_serial(clash.end_seq)})."
                )
            # Counter chỉ tiến, không lùi: lô lấp khoảng trống phía dưới không kéo
            # lô "nối tiếp" sau đó quay lại đè dải đã cấp.
            counter.last_seq = max(counter.last_seq, last)
            counter.save(update_fields=["last_seq", "updated_at"])
            batch = WarrantyCodeBatch.objects.create(
                batch_code=WarrantyCodeBatchService._next_batch_code(),
                start_seq=first,
                end_seq=last,
                quantity=quantity,
                generated_seq=first - 1,
                status=WarrantyCodeBatchStatus.ASSIGNED if product else WarrantyCodeBatchStatus.RESERVED,
                product=product,
                created_by=created_by,
                assigned_by=created_by if product else None,
                assigned_at=timezone.now() if product else None,
            )
        # Sinh mã NGOÀI transaction giữ lock counter: lô 20k mất ~6s, không được
        # chặn người khác tạo lô trong lúc đó.
        from apps.warranty_codes.tasks import generate_batch_codes

        # on_commit: worker có thể nhận task trước khi request commit (mẫu
        # apps.exports.ExportJobService.enqueue).
        enqueue = lambda: transaction.on_commit(lambda: generate_batch_codes.delay(batch.id))  # noqa: E731
        if quantity > settings.WARRANTY_CODE_SYNC_GENERATE_MAX:
            enqueue()
            return batch
        try:
            WarrantyCodeBatchService.generate_codes(batch_id=batch.id)
        except Exception:
            # Lô đã ghi, dải đã cấp; mã dở dang thì để task nền sinh nốt (resumable)
            # thay vì trả 500 và bỏ lô kẹt ở "Đang sinh x/y" không ai nhặt.
            logger.exception("sync generate failed for batch %s — handing off to task", batch.id)
            enqueue()
        batch.refresh_from_db(fields=["generated_seq"])
        return batch

    @staticmethod
    def generate_codes(*, batch_id: int) -> int:
        """Sinh nốt mã cho lô từ `generated_seq + 1` tới `end_seq`, trả về số mã đã thêm.

        Idempotent + resumable: mỗi chunk lock dòng lô, đọc lại generated_seq rồi
        insert + chốt trong cùng transaction. Hai worker chạy trùng lô (retry đè
        lượt cũ) chỉ xếp hàng chờ nhau, không sinh trùng.
        """
        inserted = 0
        while True:
            with transaction.atomic():
                # of=("self",): product là FK nullable (LEFT JOIN) — Postgres không
                # cho FOR UPDATE lên phía nullable của outer join.
                batch = (
                    WarrantyCodeBatch.objects.select_for_update(of=("self",))
                    .select_related("product")
                    .get(id=batch_id)
                )
                if batch.generated_seq >= batch.end_seq:
                    return inserted
                chunk_start = batch.generated_seq + 1
                chunk_end = min(chunk_start + _CHUNK_SIZE - 1, batch.end_seq)
                WarrantyCodeBatchService._insert_chunk(batch, batch.product, chunk_start, chunk_end)
                WarrantyCodeBatch.objects.filter(id=batch_id).update(generated_seq=chunk_end)
            inserted += chunk_end - chunk_start + 1

    @staticmethod
    def assign_product(*, batch_id: int, product_id: int, assigned_by) -> WarrantyCodeBatch:
        from apps.catalog.models import Product

        product = Product.objects.filter(id=product_id, deleted_at__isnull=True).first()
        if product is None:
            raise ValueError("Không tìm thấy sản phẩm.")
        with transaction.atomic():
            batch = WarrantyCodeBatch.objects.select_for_update().filter(id=batch_id).first()
            if batch is None:
                raise ValueError("Không tìm thấy lô mã.")
            if batch.status != WarrantyCodeBatchStatus.RESERVED:
                raise ValueError("Lô mã đã gắn sản phẩm, không thể gắn lại.")
            if batch.is_generating:
                raise ValueError("Lô đang sinh mã, chờ xong rồi gắn sản phẩm.")
            batch.product = product
            batch.status = WarrantyCodeBatchStatus.ASSIGNED
            batch.assigned_by = assigned_by
            batch.assigned_at = timezone.now()
            batch.save(update_fields=["product", "status", "assigned_by", "assigned_at"])
            WarrantyCode.objects.filter(batch=batch).update(product=product)
        return batch

    @staticmethod
    def split_batch(*, batch_id: int, cut_seq: int, user) -> tuple[WarrantyCodeBatch, WarrantyCodeBatch]:
        with transaction.atomic():
            # Lock counter trước để serialize sinh batch_code với create_batch.
            WarrantyCodeCounter.objects.select_for_update().get()
            batch = WarrantyCodeBatch.objects.select_for_update().filter(id=batch_id).first()
            if batch is None:
                raise ValueError("Không tìm thấy lô mã.")
            if batch.status != WarrantyCodeBatchStatus.RESERVED:
                raise ValueError("Chỉ tách được lô chưa gắn sản phẩm.")
            if batch.is_generating:
                raise ValueError("Lô đang sinh mã, chờ xong rồi tách.")
            if not (batch.start_seq <= cut_seq < batch.end_seq):
                raise ValueError(f"Điểm cắt phải trong khoảng {batch.start_seq} đến {batch.end_seq - 1}.")
            child = WarrantyCodeBatch.objects.create(
                batch_code=WarrantyCodeBatchService._next_batch_code(),
                start_seq=cut_seq + 1,
                end_seq=batch.end_seq,
                quantity=batch.end_seq - cut_seq,
                generated_seq=batch.end_seq,
                status=WarrantyCodeBatchStatus.RESERVED,
                parent=batch,
                created_by=user,
            )
            batch.end_seq = cut_seq
            batch.quantity = cut_seq - batch.start_seq + 1
            batch.generated_seq = cut_seq
            batch.save(update_fields=["end_seq", "quantity", "generated_seq"])
            WarrantyCode.objects.filter(batch=batch, seq__gte=child.start_seq).update(batch=child)
        return batch, child

    @staticmethod
    def _insert_chunk(batch, product, chunk_start: int, chunk_end: int) -> None:
        # Trùng mã ngẫu nhiên xác suất ~36^-CODE_LENGTH — retry sinh lại cả chunk, unique index là chốt chặn.
        for attempt in range(_MAX_CODE_RETRY):
            rows = [
                WarrantyCode(
                    seq=seq,
                    serial=format_serial(seq),
                    code=generate_code(),
                    batch=batch,
                    product=product,
                )
                for seq in range(chunk_start, chunk_end + 1)
            ]
            try:
                with transaction.atomic():
                    WarrantyCode.objects.bulk_create(rows)
                return
            except IntegrityError:
                if attempt == _MAX_CODE_RETRY - 1:
                    raise

    @staticmethod
    def _next_batch_code() -> str:
        # Chỉ gọi trong transaction đã lock counter — count theo ngày không bị race.
        prefix = f"LOT-{timezone.localdate():%Y%m%d}-"
        nth = WarrantyCodeBatch.objects.filter(batch_code__startswith=prefix).count() + 1
        return f"{prefix}{nth:03d}"
