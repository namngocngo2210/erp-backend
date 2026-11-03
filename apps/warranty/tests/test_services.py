import pytest

from apps.warranty.claim_service import WarrantyClaimService
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyClaimStatus


@pytest.mark.django_db
def test_create_claim_makes_first_journey(order_factory, django_user_model):
    order = order_factory()
    user = django_user_model.objects.create(username="u1")
    claim = WarrantyClaimService.create_claim(order.id, {"customer_report_date": None}, user)
    assert claim.journeys.count() == 1
    assert claim.journeys.first().seq == 1


@pytest.mark.django_db
def test_add_journey_increments_seq(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourneyService.add_journey(claim)
    j2 = WarrantyJourneyService.add_journey(claim)
    assert j2.seq == 2


@pytest.mark.django_db
def test_auto_status_new_to_created(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    assert j.status == JourneyStatus.NEW
    WarrantyJourneyService.update_journey(j, {"new_tracking_code": "GHN123"})
    j.refresh_from_db()
    assert j.status == JourneyStatus.CREATED


@pytest.mark.django_db
def test_update_journey_ignores_status_keys(order_factory):
    """status/return_status không còn editable — payload có cũng bị bỏ qua."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    WarrantyJourneyService.update_journey(
        j, {"status": "delivered", "return_status": "shipping", "note": "x"}
    )
    j.refresh_from_db()
    assert j.status == JourneyStatus.NEW  # không bị set tay
    assert j.return_status == ""
    assert j.note == "x"


@pytest.mark.django_db
def test_auto_status_no_downgrade_from_shipping(order_factory):
    """Journey đã ở 'shipping' (quét/cron đặt); update có tracking → rank guard giữ nguyên."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.status = JourneyStatus.SHIPPING
    j.save(update_fields=["status", "updated_at"])
    # Update KHÔNG thể mang status (không còn editable) → auto-status bị rank guard chặn.
    WarrantyJourneyService.update_journey(j, {"new_tracking_code": "X"})
    j.refresh_from_db()
    assert j.status == JourneyStatus.SHIPPING


@pytest.mark.django_db
def test_auto_status_no_downgrade_from_packed(order_factory):
    """Journey ở 'packed' (quét xuất đặt); update field khác → vẫn packed."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.status = JourneyStatus.PACKED
    j.save(update_fields=["status", "updated_at"])
    WarrantyJourneyService.update_journey(j, {"receiver_name": "Nguyễn Văn A"})
    j.refresh_from_db()
    assert j.status == JourneyStatus.PACKED


@pytest.mark.django_db
def test_complete_sets_timestamp(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None


@pytest.mark.django_db
def test_update_claim_to_completed_stamps_completed_at(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.COMPLETED)})
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None


@pytest.mark.django_db
def test_update_claim_leaving_completed_keeps_completed_at(order_factory):
    """Rời khỏi Hoàn thành giữ nguyên completed_at (lưu vết lần hoàn thành)."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    stamped = claim.completed_at
    WarrantyClaimService.update_claim(
        claim, {"status": int(WarrantyClaimStatus.WAITING_FEEDBACK)}
    )
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.WAITING_FEEDBACK
    assert claim.completed_at == stamped


@pytest.mark.django_db
def test_update_claim_completed_again_does_not_restamp(order_factory):
    """Đã có completed_at → set 9 lần nữa không ghi đè timestamp cũ."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    stamped = claim.completed_at
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.REPAIRING)})
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.COMPLETED)})
    claim.refresh_from_db()
    assert claim.completed_at == stamped


@pytest.mark.django_db
def test_update_claim_other_status_does_not_stamp(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.REPAIRING)})
    claim.refresh_from_db()
    assert claim.completed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["new_tracking_code", "recall_code", "return_tracking_code"])
def test_mark_packed_matches_all_three_codes(order_factory, field):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    setattr(j, field, "BEST-X1")
    j.save()
    got = WarrantyJourneyService.mark_packed_by_tracking("BEST-X1")
    assert got is not None and got.id == j.id
    got.refresh_from_db()
    assert got.status == JourneyStatus.PACKED


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["new_tracking_code", "recall_code", "return_tracking_code"])
def test_mark_returned_by_tracking_sets_status_and_date(order_factory, field):
    from django.utils import timezone

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    setattr(j, field, "BEST-R1")
    j.save()
    got = WarrantyJourneyService.mark_returned_by_tracking("BEST-R1")
    assert got is not None and got.id == j.id
    got.refresh_from_db()
    assert got.status == JourneyStatus.RETURNED
    assert got.warehouse_received_at == timezone.localdate()


@pytest.mark.django_db
def test_mark_returned_keeps_existing_received_date(order_factory):
    from datetime import date

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.recall_code = "BEST-R2"
    j.warehouse_received_at = date(2026, 7, 1)
    j.save()
    WarrantyJourneyService.mark_returned_by_tracking("BEST-R2")
    j.refresh_from_db()
    assert j.warehouse_received_at == date(2026, 7, 1)  # khong de ngay da co
    assert j.status == JourneyStatus.RETURNED


@pytest.mark.django_db
def test_mark_returned_unknown_code_returns_none(order_factory):
    assert WarrantyJourneyService.mark_returned_by_tracking("KHONG-CO") is None
    assert WarrantyJourneyService.mark_returned_by_tracking("") is None
