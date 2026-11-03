"""Tích hợp: quét kho khớp `new_tracking_code` của hành trình bảo hành -> packed.

- Task 5 đã viết `WarrantyJourneyService.mark_packed_by_tracking` (service boundary).
- Task 15 nối service này vào `PendingScanService._process_one` (inventory) tại
  điểm quét KHÔNG khớp Order: nếu kind=dispatch và mã khớp `new_tracking_code`
  của 1 journey chưa packed+ -> set journey packed, đồng thời đóng luôn dòng
  PendingShipmentScan (status=matched) thay vì để treo "chờ khớp" mãi.
"""
import pytest

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan
from apps.warehouses.models import Warehouse
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyJourney

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho test bảo hành", code="KTBH01")


def test_scan_marks_journey_packed(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1, new_tracking_code="GHN777", status="created")
    result = WarrantyJourneyService.mark_packed_by_tracking("GHN777")
    assert result is not None
    j.refresh_from_db()
    assert j.status == JourneyStatus.PACKED


def test_scan_unknown_code_noop():
    assert WarrantyJourneyService.mark_packed_by_tracking("NOPE") is None


@pytest.mark.parametrize(
    "status",
    [JourneyStatus.RETURNED, JourneyStatus.PENDING_RETURN, JourneyStatus.DELIVERED,
     JourneyStatus.LOST, JourneyStatus.SHIPPING, JourneyStatus.PACKED],
)
def test_scan_does_not_demote_journey_at_or_above_packed_rank(order_factory, status):
    """rank(status) >= rank(PACKED) -> mã quét lại KHÔNG được hạ cấp về packed."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(
        claim=claim, seq=1, new_tracking_code="GHN-RANK", status=status
    )
    result = WarrantyJourneyService.mark_packed_by_tracking("GHN-RANK")
    assert result is None
    j.refresh_from_db()
    assert j.status == status


@pytest.mark.parametrize("status", [JourneyStatus.NEW, JourneyStatus.CREATED])
def test_scan_promotes_journey_below_packed_rank(order_factory, status):
    """rank(status) < rank(PACKED) -> quét mã vẫn nâng cấp lên packed bình thường."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(
        claim=claim, seq=1, new_tracking_code="GHN-PROMOTE", status=status
    )
    result = WarrantyJourneyService.mark_packed_by_tracking("GHN-PROMOTE")
    assert result is not None
    j.refresh_from_db()
    assert j.status == JourneyStatus.PACKED


def test_pending_scan_dispatch_matches_warranty_journey(order_factory, warehouse):
    """Quét mã trùng new_tracking_code (kind=dispatch, không có Order nào khớp)
    -> journey chuyển packed VÀ dòng PendingShipmentScan được đóng lại (matched),
    không còn treo trong danh sách "chờ khớp" của NV kho."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourney.objects.create(
        claim=claim, seq=1, new_tracking_code="GHN999", status="created"
    )
    scan = PendingShipmentScan.objects.create(
        code="GHN999", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
    )

    summary = PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    scan.refresh_from_db()
    assert journey.status == JourneyStatus.PACKED
    assert summary["matched"] == 1
    assert scan.status == PendingScanStatus.MATCHED
    assert "warranty-journey" in scan.result_ref


def test_pending_scan_return_kind_matches_warranty_journey(order_factory, warehouse):
    """Quét NHẬP HOÀN (kind=return) khớp mã hành trình BH -> kho đã nhận hàng:
    journey chuyển returned + ghi ngày kho nhận, scan đóng (matched), KHÔNG ghi sổ tồn."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourney.objects.create(
        claim=claim, seq=1, recall_code="BH-RET-1", status="shipping"
    )
    scan = PendingShipmentScan.objects.create(
        code="BH-RET-1", kind=PendingScanKind.RETURN, warehouse=warehouse,
    )

    summary = PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    scan.refresh_from_db()
    assert journey.status == JourneyStatus.RETURNED
    assert journey.warehouse_received_at is not None
    assert summary["matched"] == 1
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.result_ref == f"warranty-journey:{journey.id}"
    assert "đã nhận hàng hoàn" in scan.error


def test_pending_scan_dispatch_matches_return_tracking_code(order_factory, warehouse):
    """Quét XUẤT khớp mã gửi lại khách (chặng gửi lại) cũng set packed."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourney.objects.create(
        claim=claim, seq=1, return_tracking_code="BH-RESEND-1", status="created"
    )
    PendingShipmentScan.objects.create(
        code="BH-RESEND-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
    )

    summary = PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    assert journey.status == JourneyStatus.PACKED
    assert summary["matched"] == 1
