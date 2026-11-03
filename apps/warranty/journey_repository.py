from __future__ import annotations

from django.db.models import Max

from apps.warranty.models import WarrantyJourney


class WarrantyJourneyRepository:
    """Truy vấn hành trình bảo hành (chỉ tầng đọc/ghi DB)."""

    @staticmethod
    def base_qs():
        return WarrantyJourney.objects.select_related("product", "created_by")

    @classmethod
    def for_claim(cls, claim_id: int) -> list[WarrantyJourney]:
        return list(cls.base_qs().filter(claim_id=claim_id).order_by("seq"))

    @classmethod
    def get(cls, journey_id: int) -> WarrantyJourney | None:
        return cls.base_qs().filter(id=journey_id).first()

    @staticmethod
    def next_seq(claim_id: int) -> int:
        agg = WarrantyJourney.objects.filter(claim_id=claim_id).aggregate(m=Max("seq"))
        return (agg["m"] or 0) + 1
