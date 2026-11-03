from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus

class WarrantyOrderNotFound(Exception):
    """Không tìm thấy đơn theo mã vận đơn."""


class WarrantyOrderAmbiguous(Exception):
    """Nhiều đơn cùng mã vận đơn, không thể xác định."""


class WarrantyClaimExists(Exception):
    """Đơn đã có phiếu bảo hành."""


_CLAIM_EDITABLE_FIELDS = (
    "customer_report_date",
    "activation_date",
    "warranty_months",
    "status",
)


class WarrantyClaimService:
    """Nghiệp vụ phiếu bảo hành: tạo/sửa/xóa mềm + ràng buộc 1 phiếu / đơn."""

    @staticmethod
    def create_claim(order_id: int, data: dict, created_by) -> WarrantyClaim:
        order = Order.objects.filter(id=order_id, deleted_at__isnull=True).first()
        if order is None:
            raise ValueError("Không tìm thấy đơn hàng.")
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        fields = {k: data[k] for k in _CLAIM_EDITABLE_FIELDS if k in data}
        try:
            with transaction.atomic():
                claim = WarrantyClaim.objects.create(order=order, created_by=created_by, **fields)
                WarrantyJourneyService.add_journey(claim, created_by=created_by)
                return claim
        except IntegrityError as exc:
            # Race hoặc order đã có claim đã soft-delete (unique constraint vẫn tính) → coi là Exists.
            raise WarrantyClaimExists() from exc

    @staticmethod
    def ingest_claim_by_shipping_code(shipping_code: str, data: dict) -> WarrantyClaim:
        """Tạo phiếu bảo hành + journey seq=1 từ payload ingest công khai.

        `data` (đã pop `shipping_code` ở view) map thẳng vào journey đầu tiên qua
        `WarrantyJourneyService.update_journey` — chỉ các key thuộc
        `JOURNEY_EDITABLE_FIELDS` được nhận, key lạ bị bỏ qua an toàn.
        """
        qs = Order.objects.filter(shipping_code=shipping_code, deleted_at__isnull=True)
        count = qs.count()
        if count == 0:
            raise WarrantyOrderNotFound()
        if count > 1:
            raise WarrantyOrderAmbiguous()
        order = qs.first()
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        try:
            with transaction.atomic():
                claim = WarrantyClaim.objects.create(order=order, created_by=None)
                journey = WarrantyJourneyService.add_journey(claim)
                WarrantyJourneyService.update_journey(journey, data)
                return claim
        except IntegrityError as exc:
            raise WarrantyClaimExists() from exc

    @staticmethod
    def update_claim(claim: WarrantyClaim, data: dict) -> WarrantyClaim:
        changed = []
        for key in _CLAIM_EDITABLE_FIELDS:
            if key in data:
                setattr(claim, key, data[key])
                changed.append(key)
        # NV chọn tay sang Hoàn thành → stamp completed_at như endpoint /complete.
        # Không stamp lại nếu đã có (lưu vết lần hoàn thành đầu tiên).
        if (
            "status" in changed
            and claim.status == WarrantyClaimStatus.COMPLETED
            and claim.completed_at is None
        ):
            claim.completed_at = timezone.now()
            changed.append("completed_at")
        if changed:
            claim.save(update_fields=[*changed, "updated_at"])
        return claim

    @staticmethod
    def complete(claim: WarrantyClaim) -> WarrantyClaim:
        claim.status = WarrantyClaimStatus.COMPLETED
        claim.completed_at = timezone.now()
        claim.save(update_fields=["status", "completed_at", "updated_at"])
        return claim

    @staticmethod
    def soft_delete(claim: WarrantyClaim) -> None:
        claim.deleted_at = timezone.now()
        claim.save(update_fields=["deleted_at", "updated_at"])

    @staticmethod
    def bulk_soft_delete(ids: list[int]) -> int:
        """Xóa mềm hàng loạt — bỏ qua id không tồn tại hoặc đã xóa. Trả về số phiếu đã xóa."""
        now = timezone.now()
        return WarrantyClaim.objects.filter(id__in=ids, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
