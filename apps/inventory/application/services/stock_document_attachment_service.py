from __future__ import annotations

import logging
import uuid

from django.db import transaction

from apps.inventory.infrastructure import document_storage
from apps.inventory.models import StockDocument, StockDocumentAttachment

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB

_MAGIC_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # kèm check data[8:12] == b"WEBP"
    (b"%PDF-", "application/pdf"),
)


def _sniff_content_type(data: bytes) -> str | None:
    for magic, ctype in _MAGIC_SIGNATURES:
        if data.startswith(magic):
            if ctype == "image/webp" and data[8:12] != b"WEBP":
                continue
            return ctype
    return None


class AttachmentValidationError(ValueError):
    """Raised when an uploaded file fails type/size validation."""


class StockDocumentAttachmentService:
    @staticmethod
    def add_attachment(
        *,
        document_id: int,
        data: bytes,
        content_type: str,
        original_name: str = "",
        user_id: int | None = None,
    ) -> StockDocumentAttachment:
        # content_type from the client is only a hint; the stored type is
        # whatever the magic bytes actually sniff to.
        sniffed = _sniff_content_type(data)
        ext = ALLOWED_CONTENT_TYPES.get(sniffed) if sniffed else None
        if ext is None:
            raise AttachmentValidationError(
                "Chỉ chấp nhận ảnh (JPG/PNG/WebP) hoặc PDF."
            )
        if len(data) > MAX_FILE_BYTES:
            raise AttachmentValidationError("File quá lớn (tối đa 10MB).")

        # Ensure the document exists before writing to MinIO.
        StockDocument.objects.get(id=document_id)

        # Upload MinIO TRƯỚC khi mở transaction: round-trip mạng có thể mất hàng
        # phút khi MinIO chậm, mà idle_in_transaction_session_timeout=60s sẽ kill
        # transaction giữ connection lâu như vậy. Transaction bên dưới chỉ ghi DB.
        key = f"stock-documents/{document_id}/{uuid.uuid4().hex}.{ext}"
        document_storage.put_object(key, data, sniffed)
        try:
            with transaction.atomic():
                return StockDocumentAttachment.objects.create(
                    document_id=document_id,
                    file_key=key,
                    content_type=sniffed,
                    original_name=original_name[:255],
                    size_bytes=len(data),
                    uploaded_by_id=user_id,
                )
        except Exception:
            # DB hỏng sau khi upload đã thành công → best-effort dọn object mồ côi.
            # Object mồ côi chấp nhận được, transaction treo/hỏng thì không.
            try:
                document_storage.delete_object(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Không dọn được object MinIO mồ côi %s sau khi ghi DB thất bại", key
                )
            raise

    @staticmethod
    @transaction.atomic
    def delete_attachment(*, attachment_id: int) -> int:
        att = StockDocumentAttachment.objects.select_for_update().get(id=attachment_id)
        document_id = att.document_id
        key = att.file_key
        att.delete()

        def _cleanup_object():
            # Best-effort; object mồ côi chấp nhận được, không fail request.
            try:
                document_storage.delete_object(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Không xoá được object MinIO %s sau khi xoá attachment", key
                )

        # Xoá object S3 SAU khi transaction commit (on_commit): không giữ row-lock
        # qua round-trip mạng, và nếu transaction rollback thì object vẫn còn khớp
        # với row chưa bị xoá.
        transaction.on_commit(_cleanup_object)
        return document_id
