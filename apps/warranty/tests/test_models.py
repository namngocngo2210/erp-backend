from datetime import date
from unittest import mock

import pytest

from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus, WarrantyJourney, JourneyStatus


@pytest.mark.django_db
def test_claim_defaults(order_factory):
    order = order_factory()
    claim = WarrantyClaim.objects.create(order=order)
    assert claim.status == WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM == 1
    assert claim.customer_report_date is None
    assert claim.completed_at is None
    assert claim.deleted_at is None


def test_claim_status_flow_choices():
    """Bộ 12 trạng thái phiếu theo flow CSKH — 1-11 đánh theo thứ tự flow, 12 thêm sau."""
    expected = {
        1: "Đợi khách XN",
        2: "Chờ tạo đơn",
        3: "Chờ vận chuyển",
        4: "Đang sửa chữa",
        5: "Chờ chốt phương án",
        6: "Chờ tạo đơn gửi lại",
        7: "Đang gửi lại khách",
        8: "Xin feedback",
        9: "Hoàn thành",
        10: "Thất lạc",
        11: "Hủy",
        12: "Đang vận chuyển",
    }
    assert dict(WarrantyClaimStatus.choices) == expected
    assert WarrantyClaimStatus.COMPLETED == 9
    assert WarrantyClaimStatus.CANCELLED == 11


@pytest.mark.django_db
def test_journey_defaults(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1)
    assert j.status == JourneyStatus.NEW
    assert j.declared_value == 0
    assert j.receiver_name == ""
    assert j.claim.journeys.count() == 1


@pytest.mark.django_db
def test_journey_seq_unique_per_claim(order_factory):
    from django.db import IntegrityError

    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim, seq=1)
    with pytest.raises(IntegrityError):
        WarrantyJourney.objects.create(claim=claim, seq=1)


@pytest.mark.django_db
def test_claim_warranty_defaults(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    assert claim.activation_date is None
    assert claim.warranty_months == 24
    assert claim.expiry_date is None
    assert claim.warranty_days_left is None


@pytest.mark.django_db
def test_journey_tracking_codes_are_stripped_on_save(order_factory):
    """Finding #8 (review 2026-07-22): subscriber realtime lọc DB bằng `__in`
    (exact match, giữ index) trong khi cron so bằng Python có `.strip()`. Mã
    lưu kèm khoảng trắng sẽ đồng bộ được qua cron nhưng không bao giờ khớp
    được ở subscriber. Chuẩn hoá ngay khi lưu để 2 đường không lệch nhau.
    """
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(
        claim=claim,
        seq=1,
        new_tracking_code=" B-NEW ",
        recall_code="\tB-RC\n",
        return_tracking_code="B-RT",
    )
    j.refresh_from_db()
    assert j.new_tracking_code == "B-NEW"
    assert j.recall_code == "B-RC"
    assert j.return_tracking_code == "B-RT"


@pytest.mark.django_db
def test_expiry_date_is_activation_plus_months(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory(), activation_date=date(2026, 1, 15))
    assert claim.expiry_date == date(2028, 1, 15)

    claim.warranty_months = 12
    assert claim.expiry_date == date(2027, 1, 15)


@pytest.mark.django_db
def test_expiry_date_handles_leap_day(order_factory):
    claim = WarrantyClaim.objects.create(
        order=order_factory(), activation_date=date(2024, 2, 29), warranty_months=12
    )
    assert claim.expiry_date == date(2025, 2, 28)


@pytest.mark.django_db
def test_warranty_days_left_counts_from_today(order_factory):
    today = date(2026, 7, 11)
    claim = WarrantyClaim.objects.create(order=order_factory(), activation_date=date(2026, 7, 1))
    with mock.patch("apps.warranty.models.timezone.localdate", return_value=today):
        assert claim.warranty_days_left == (date(2028, 7, 1) - today).days


@pytest.mark.django_db
def test_warranty_days_left_negative_when_expired(order_factory):
    today = date(2026, 7, 11)
    claim = WarrantyClaim.objects.create(order=order_factory(), activation_date=date(2020, 1, 1))
    with mock.patch("apps.warranty.models.timezone.localdate", return_value=today):
        assert claim.warranty_days_left < 0


@pytest.mark.django_db
def test_journey_best_status_ms_defaults_to_zero(order_factory):
    from apps.warranty.journey_service import WarrantyJourneyService
    from apps.warranty.models import WarrantyClaim

    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)

    assert journey.new_tracking_best_status_ms == 0
    assert journey.recall_best_status_ms == 0
    assert journey.return_tracking_best_status_ms == 0

    # Epoch millisecond vượt trần IntegerField 32-bit (2_147_483_647) — phải lưu được.
    journey.new_tracking_best_status_ms = 1_780_000_000_000
    journey.save(update_fields=["new_tracking_best_status_ms"])
    journey.refresh_from_db()
    assert journey.new_tracking_best_status_ms == 1_780_000_000_000
