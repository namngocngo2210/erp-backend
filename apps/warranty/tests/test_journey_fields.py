"""Field-level của WarrantyJourney: warehouse_received_at + nhóm field feedback/claim.

Kho nhận hàng bảo hành ghi theo NGÀY QUÉT, không phải ngày cron khớp.
"""
from datetime import date, datetime, UTC

import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import (
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
)
from apps.users.tests.helpers import make_user_with_sections
from apps.warehouses.models import Warehouse
from apps.warranty.journey_service import JOURNEY_EDITABLE_FIELDS, WarrantyJourneyService
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyJourney
from apps.warranty.received_at_backfill import backfill_warehouse_received_at

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# warehouse_received_at theo ngày quét (từ test_journey_received_at_scan_date.py)
# ---------------------------------------------------------------------------

# 03/07 17:30 UTC = 04/07 00:30 giờ VN — bắt lỗi dùng `.date()` trên giá trị UTC.
SCANNED_AT = datetime(2026, 7, 3, 17, 30, tzinfo=UTC)
SCANNED_LOCAL_DATE = date(2026, 7, 4)
# Lần quét lại của CÙNG mã, một ngày sau — dùng cho ca trùng bản ghi quét.
RESCANNED_AT = datetime(2026, 7, 4, 17, 30, tzinfo=UTC)
RESCANNED_LOCAL_DATE = date(2026, 7, 5)
# 05/07 17:30 UTC = 06/07 00:30 giờ VN — ngày cron khớp cũng phải LỆCH giữa hai
# múi giờ, nếu không vế `AT TIME ZONE 'Asia/Bangkok'` trong mệnh đề chặn của
# backfill có bị xoá đi thì test vẫn xanh (Django ghim session DB ở UTC).
MATCHED_AT = datetime(2026, 7, 5, 17, 30, tzinfo=UTC)
MATCHED_LOCAL_DATE = date(2026, 7, 6)


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho BH", code="KBH1")


@pytest.fixture
def journey(order_factory):
    # order_factory dựng Order tối thiểu kèm Channel/Customer (conftest của app).
    # Order đó có shipping_code rỗng nên KHÔNG khớp mã quét "BHX1" — mã phải rơi
    # xuống đúng nhánh dò hành trình bảo hành trong `_process_one`.
    claim = WarrantyClaim.objects.create(order=order_factory())
    return WarrantyJourney.objects.create(
        claim=claim, seq=1, recall_code="BHX1", status=JourneyStatus.CREATED
    )


def test_return_scan_sets_received_at_to_scan_date(journey, warehouse):
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)

    PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    assert journey.status == JourneyStatus.RETURNED
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE


def test_existing_received_at_is_not_overwritten(journey, warehouse):
    journey.warehouse_received_at = date(2026, 6, 30)
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)

    PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    assert journey.warehouse_received_at == date(2026, 6, 30)


def test_backfill_dry_run_reports_without_writing(journey, warehouse):
    """Mặc định (`apply=False`) chỉ BÁO CÁO — không được đụng một dòng nào.

    Đây là chế độ migration 0016 chạy: trường này người dùng sửa được nên deploy
    không được tự ghi đè.
    """
    journey.warehouse_received_at = MATCHED_LOCAL_DATE
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        result_ref=f"warranty-journey:{journey.id}",
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    candidates = backfill_warehouse_received_at(connection)

    assert candidates == [(journey.id, MATCHED_LOCAL_DATE, SCANNED_LOCAL_DATE)]
    # KHÔNG dòng nào trong DB đổi — kiểm cả bảng, không chỉ hành trình đang xét.
    journey.refresh_from_db()
    assert journey.warehouse_received_at == MATCHED_LOCAL_DATE
    assert list(
        WarrantyJourney.objects.values_list("id", "warehouse_received_at")
    ) == [(journey.id, MATCHED_LOCAL_DATE)]
    # Chạy lại vẫn ra đúng tập đó (dry-run không tự tiêu hoá ứng viên).
    assert backfill_warehouse_received_at(connection) == candidates


def test_backfill_apply_fixes_value_written_by_cron(journey, warehouse):
    """`apply=True` nắn về ngày quét, đúng tập dòng mà dry-run đã báo."""
    journey.warehouse_received_at = MATCHED_LOCAL_DATE
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        result_ref=f"warranty-journey:{journey.id}",
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    # Rà trước rồi mới nắn: hai chế độ dùng chung mệnh đề FROM/WHERE nên tập
    # hành trình phải TRÙNG KHÍT — nếu lệch thì thứ người vận hành duyệt khác
    # thứ thực sự bị đè.
    candidates = backfill_warehouse_received_at(connection, apply=False)
    repaired = backfill_warehouse_received_at(connection, apply=True)

    assert {row[0] for row in repaired} == {row[0] for row in candidates}
    journey.refresh_from_db()
    # Nhật ký từng dòng (id, ngày cũ, ngày mới) — dấu vết duy nhất để lần ngược
    # vì chiều reverse của migration là noop.
    assert repaired == [(journey.id, MATCHED_LOCAL_DATE, SCANNED_LOCAL_DATE)]
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE

    # Chạy lần hai không nắn thêm gì (idempotent), và cũng hết ứng viên.
    assert backfill_warehouse_received_at(connection, apply=True) == []
    assert backfill_warehouse_received_at(connection) == []
    journey.refresh_from_db()
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE


def test_backfill_picks_earliest_matched_scan(journey, warehouse):
    """Hai bản ghi quét đã khớp cho CÙNG hành trình → phải lấy lần quét SỚM NHẤT.

    `uniq_pending_scan_code_kind` chỉ là unique index PARTIAL (status='pending')
    nên quét lại một mã sau khi dòng đầu đã sang `matched` sinh ra dòng matched
    thứ hai cùng `result_ref`. Dòng quét MUỘN được tạo TRƯỚC (id nhỏ hơn) để nếu
    câu lệnh quay về `UPDATE ... FROM` trần thì Postgres nhặt đúng dòng sai.
    """
    ref = f"warranty-journey:{journey.id}"
    journey.warehouse_received_at = MATCHED_LOCAL_DATE
    journey.save(update_fields=["warehouse_received_at"])
    later = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, result_ref=ref,
    )
    earlier = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, result_ref=ref,
    )
    # Cả hai cùng được cron khớp trong một ngày ⇒ cùng thoả mệnh đề chặn.
    PendingShipmentScan.objects.filter(pk=later.pk).update(
        created_at=RESCANNED_AT, updated_at=MATCHED_AT
    )
    PendingShipmentScan.objects.filter(pk=earlier.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    repaired = backfill_warehouse_received_at(connection, apply=True)

    journey.refresh_from_db()
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE
    assert journey.warehouse_received_at != RESCANNED_LOCAL_DATE
    # Một hành trình = một dòng nắn, không nhân đôi theo số bản ghi quét.
    assert repaired == [(journey.id, MATCHED_LOCAL_DATE, SCANNED_LOCAL_DATE)]


@pytest.mark.parametrize("apply", [False, True])
def test_backfill_keeps_user_edited_value(journey, warehouse, apply):
    """Người dùng đã sửa tay → không khớp ngày cron ghi → giữ nguyên ở CẢ HAI chế độ.

    Hai chế độ dùng chung `_CANDIDATES_FROM_WHERE`; test chạy cả hai để nếu ai đó
    sửa lệch một bên thì vỡ ngay.
    """
    journey.warehouse_received_at = date(2026, 6, 30)
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        result_ref=f"warranty-journey:{journey.id}",
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    repaired = backfill_warehouse_received_at(connection, apply=apply)

    journey.refresh_from_db()
    assert repaired == []
    assert journey.warehouse_received_at == date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Nhóm field feedback/claim (từ test_journey_feedback_claim_fields.py)
# ---------------------------------------------------------------------------

_NEW_FIELDS = (
    "feedback_agree_review",
    "feedback_rating",
    "feedback_note",
    "claim_date",
    "claim_note",
    "carrier_response",
    "compensation_status",
    "compensation_amount",
)


def test_new_fields_are_editable():
    for field in _NEW_FIELDS:
        assert field in JOURNEY_EDITABLE_FIELDS, field


@pytest.mark.django_db
def test_new_fields_default_to_empty(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)

    assert journey.feedback_agree_review == ""
    assert journey.feedback_rating == ""
    assert journey.feedback_note == ""
    assert journey.claim_date is None
    assert journey.claim_note == ""
    assert journey.carrier_response == ""
    assert journey.compensation_status == ""
    # So khớp với declared_value/cod_amount ở test_models.py: default=0 chưa
    # refresh_from_db() vẫn là Python int, chỉ thành Decimal("0.00") sau round-trip DB.
    assert journey.compensation_amount == 0


@pytest.mark.django_db
def test_patch_saves_feedback_and_claim(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)
    client = APIClient()
    client.force_authenticate(make_user_with_sections("wty-feedback", ["warranty"]))

    response = client.patch(
        f"/api/warranty/journeys/{journey.id}",
        {
            "feedback_agree_review": "yes",
            "feedback_rating": "lost_trust",
            "feedback_note": "Khách phàn nàn giao chậm",
            "claim_date": "2026-07-20",
            "claim_note": "Best báo mất kiện",
            "carrier_response": "agreed",
            "compensation_status": "paid",
            "compensation_amount": "350000.00",
        },
        format="json",
    )

    assert response.status_code == 200
    journey.refresh_from_db()
    assert journey.feedback_agree_review == "yes"
    assert journey.feedback_rating == "lost_trust"
    assert journey.feedback_note == "Khách phàn nàn giao chậm"
    assert str(journey.claim_date) == "2026-07-20"
    assert journey.claim_note == "Best báo mất kiện"
    assert journey.carrier_response == "agreed"
    assert journey.compensation_status == "paid"
    assert str(journey.compensation_amount) == "350000.00"
    # Serializer trả lại đủ 8 field để FE đọc.
    for field in _NEW_FIELDS:
        assert field in response.data, field


@pytest.mark.django_db
def test_invalid_enum_value_rejected(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)
    client = APIClient()
    client.force_authenticate(make_user_with_sections("wty-feedback-2", ["warranty"]))

    response = client.patch(
        f"/api/warranty/journeys/{journey.id}",
        {"feedback_rating": "khong_ton_tai"},
        format="json",
    )

    assert response.status_code == 400
