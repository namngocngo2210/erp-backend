"""Nghiệp vụ nội dung trang sản phẩm: ảnh (MinIO) và thứ tự hiển thị.

Mirror `apps/inventory/application/services/stock_document_attachment_service.py`
(magic-byte sniffing, upload MinIO trước transaction, dọn object mồ côi, xoá MinIO
qua on_commit) — cùng lớp rủi ro, cùng cách xử lý.
"""
from __future__ import annotations

import logging
import uuid

from django.db import transaction

from apps.orders.infrastructure import product_storage
from apps.catalog.models import ProductProfile, ProductProfileImage

logger = logging.getLogger(__name__)

MAX_IMAGES = 8
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5MB

_ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # kèm check data[8:12] == b"WEBP"
)


class ProfileImageValidationError(ValueError):
    """Ảnh upload không hợp lệ (định dạng, dung lượng, hoặc quá số lượng)."""


def _sniff(data: bytes) -> str | None:
    for magic, ctype in _MAGIC:
        if data.startswith(magic):
            if ctype == "image/webp" and data[8:12] != b"WEBP":
                continue
            return ctype
    return None


class ProductProfileService:
    @staticmethod
    def add_image(*, product_id: int, data: bytes) -> ProductProfileImage:
        # content_type client gửi chỉ là gợi ý — tin magic bytes, không tin header.
        sniffed = _sniff(data)
        ext = _ALLOWED.get(sniffed) if sniffed else None
        if ext is None:
            raise ProfileImageValidationError("Chỉ chấp nhận ảnh JPG, PNG hoặc WebP.")
        if len(data) > MAX_FILE_BYTES:
            raise ProfileImageValidationError("Ảnh quá lớn (tối đa 5MB).")

        profile, _ = ProductProfile.objects.get_or_create(product_id=product_id)
        # BIẾT VÀ CHẤP NHẬN: race TOCTOU ở đây — 2 upload đồng thời có thể cùng
        # đọc count() < 8 rồi cùng ghi, vượt trần 8 ảnh. CRM chỉ vài chục nhân
        # viên thao tác tuần tự nên rủi ro thực tế thấp; khoá đúng cách cần
        # select_for_update() trên profile, chưa làm vì chưa đáng đổi độ phức tạp.
        count = profile.images.count()
        if count >= MAX_IMAGES:
            raise ProfileImageValidationError(f"Tối đa {MAX_IMAGES} ảnh cho mỗi sản phẩm.")

        # Upload MinIO TRƯỚC khi mở transaction: round-trip mạng có thể mất hàng
        # phút khi MinIO chậm, mà idle_in_transaction_session_timeout sẽ kill
        # transaction giữ connection lâu như vậy. Transaction bên dưới chỉ ghi DB.
        key = f"product-media/{product_id}/{uuid.uuid4().hex}.{ext}"
        product_storage.put_object(key, data, sniffed)
        try:
            with transaction.atomic():
                return ProductProfileImage.objects.create(
                    profile=profile, object_key=key, content_type=sniffed, sort_order=count
                )
        except Exception:
            # DB hỏng sau khi upload đã thành công → best-effort dọn object mồ côi.
            # Object mồ côi chấp nhận được, transaction treo/hỏng thì không.
            try:
                product_storage.delete_object(key)
            except Exception:  # noqa: BLE001
                logger.warning("Không dọn được ảnh mồ côi %s", key)
            raise

    @staticmethod
    @transaction.atomic
    def delete_image(*, image_id: int) -> None:
        image = ProductProfileImage.objects.select_for_update().get(id=image_id)
        key = image.object_key
        image.delete()

        def _cleanup_object():
            # Best-effort; object mồ côi chấp nhận được, không fail request.
            try:
                product_storage.delete_object(key)
            except Exception:  # noqa: BLE001
                logger.warning("Không xoá được ảnh MinIO %s", key)

        # Xoá object MinIO SAU khi transaction commit (on_commit): không giữ
        # row-lock qua round-trip mạng, và nếu transaction rollback thì object vẫn
        # còn khớp với row chưa bị xoá.
        transaction.on_commit(_cleanup_object)

    @staticmethod
    @transaction.atomic
    def reorder_images(*, product_id: int, image_ids: list[int]) -> None:
        images = {
            img.id: img
            for img in ProductProfileImage.objects.select_for_update().filter(
                profile__product_id=product_id
            )
        }
        for order, image_id in enumerate(image_ids):
            image = images.get(image_id)
            if image is not None:
                image.sort_order = order
        ProductProfileImage.objects.bulk_update(images.values(), ["sort_order"])
