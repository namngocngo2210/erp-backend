from __future__ import annotations

from datetime import date

from django.db.models import Q

from apps.warranty.journey_repository import WarrantyJourneyRepository
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyJourney

# `status`/`return_status` KHÔNG editable qua API: trạng thái VC hiển thị suy tự
# động từ mã Best (tracking_status), trạng thái nội bộ do quét kho/auto đặt.
JOURNEY_EDITABLE_FIELDS = (
    "name",
    "receiver_name", "receiver_phone", "receiver_raw_address",
    "product", "send_reason", "classification", "error_category",
    "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
    "order_date", "new_tracking_code", "recall_code", "note",
    "warehouse_received_at", "inspection_result", "warehouse_check",
    "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
    "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
    "feedback_agree_review", "feedback_rating", "feedback_note",
    "claim_date", "claim_note", "carrier_response",
    "compensation_status", "compensation_amount",
)

# Cấp bậc trạng thái để không auto hạ cấp. new/created là tầng auto (theo mã
# vận đơn); các trạng thái thủ công/return đều > created nên apply_auto_status
# không đụng vào.
_STATUS_RANK = {
    JourneyStatus.NEW: 0,
    JourneyStatus.CREATED: 1,
    JourneyStatus.PACKED: 2,
    JourneyStatus.SHIPPING: 3,
    JourneyStatus.LOST: 4,
    JourneyStatus.DELIVERED: 4,
    JourneyStatus.PENDING_RETURN: 4,
    JourneyStatus.RETURNED: 5,
}


class WarrantyJourneyService:
    """Nghiệp vụ hành trình bảo hành: tạo/sửa/xóa + auto-status theo tracking code."""

    @staticmethod
    def add_journey(claim: WarrantyClaim, created_by=None) -> WarrantyJourney:
        seq = WarrantyJourneyRepository.next_seq(claim.id)
        return WarrantyJourney.objects.create(claim=claim, seq=seq, created_by=created_by)

    @staticmethod
    def apply_auto_status(journey: WarrantyJourney) -> None:
        """new↔created theo new_tracking_code; không đụng nếu đã ở mức > created."""
        rank = _STATUS_RANK.get(journey.status, 0)
        if rank > _STATUS_RANK[JourneyStatus.CREATED]:
            return
        if journey.new_tracking_code.strip():
            journey.status = JourneyStatus.CREATED
        else:
            journey.status = JourneyStatus.NEW

    @classmethod
    def update_journey(cls, journey: WarrantyJourney, data: dict) -> WarrantyJourney:
        changed = []
        for key in JOURNEY_EDITABLE_FIELDS:
            if key in data:
                setattr(journey, key, data[key])
                changed.append(key)
        # status không còn editable qua API — luôn auto suy new/created theo mã.
        before = journey.status
        cls.apply_auto_status(journey)
        if journey.status != before:
            changed.append("status")
        if changed:
            journey.save(update_fields=[*changed, "updated_at"])
        return journey

    @staticmethod
    def delete_journey(journey: WarrantyJourney) -> None:
        journey.delete()

    @classmethod
    def mark_packed_by_tracking(cls, code: str) -> WarrantyJourney | None:
        """Quét XUẤT kho khớp 1 trong 3 mã của hành trình → đã đóng hàng.

        Chỉ nâng cấp journey đang ở rank thấp hơn PACKED (new/created); journey
        đã ở packed trở lên không bị hạ cấp khi mã được quét lại.
        """
        code = (code or "").strip()
        if not code:
            return None
        packed_rank = _STATUS_RANK[JourneyStatus.PACKED]
        promotable_statuses = [
            status for status, rank in _STATUS_RANK.items() if rank < packed_rank
        ]
        journey = (
            WarrantyJourney.objects.filter(
                Q(new_tracking_code=code) | Q(recall_code=code) | Q(return_tracking_code=code),
                status__in=promotable_statuses,
            )
            .order_by("-id")
            .first()
        )
        if journey is None:
            return None
        journey.status = JourneyStatus.PACKED
        journey.save(update_fields=["status", "updated_at"])
        return journey

    @staticmethod
    def mark_returned_by_tracking(
        code: str, received_at: date | None = None
    ) -> WarrantyJourney | None:
        """Quét HÀNG HOÀN khớp 1 trong 3 mã của hành trình → kho đã nhận hàng BH.

        Set trạng thái nội bộ `returned` + ngày kho nhận (nếu trống). KHÔNG ghi
        sổ tồn — hàng BH nhập kho qua phiếu kho riêng.

        ``received_at``: ngày kho THỰC SỰ nhận hàng. Luồng quét truyền ngày QUÉT
        (mã quét ngày D, cron khớp được ở ngày D+n thì kho vẫn nhận ngày D).
        Mặc định None ⇒ hôm nay, giữ nguyên hành vi cho caller khác.
        """
        from django.utils import timezone

        code = (code or "").strip()
        if not code:
            return None
        journey = (
            WarrantyJourney.objects.filter(
                Q(new_tracking_code=code) | Q(recall_code=code) | Q(return_tracking_code=code)
            )
            .order_by("-id")
            .first()
        )
        if journey is None:
            return None
        journey.status = JourneyStatus.RETURNED
        update_fields = ["status", "updated_at"]
        if journey.warehouse_received_at is None:
            journey.warehouse_received_at = received_at or timezone.localdate()
            update_fields.append("warehouse_received_at")
        journey.save(update_fields=update_fields)
        return journey
