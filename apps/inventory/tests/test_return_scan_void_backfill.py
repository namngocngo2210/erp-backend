"""Backfill thu hồi dòng quét nhập hoàn đã chết (migration 0036).

Dựng lại đúng ca prod của mã 84859735506962: bắn 3 lần, huỷ 2 lần đầu, lần 3 còn
sống — màn hình vẫn hiện đủ 3 dòng, mỗi dòng mượn SKU × SL của nhóm đơn còn sống.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from apps.inventory.application.services.return_scan_void_backfill import (
    void_dead_return_scans,
)
from apps.inventory.models import (
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    ReturnStatus,
)
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def note(warehouse):
    return ReturnNote.objects.create(
        code="PTH-BF-1", warehouse=warehouse, doc_date=timezone.localdate()
    )


def _scan(warehouse, code, *, status=PendingScanStatus.MATCHED, result_ref=""):
    return PendingShipmentScan.objects.create(
        code=code, kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=status, result_ref=result_ref,
    )


def _entry(note, code, status):
    return ReturnEntry.objects.create(
        return_note=note, shipping_code=code, status=status
    )


def test_keeps_newest_scan_when_entry_still_alive(warehouse, note):
    """Ca prod 84859735506962: 3 lần bắn, chỉ lần cuối còn sống."""
    first = _scan(warehouse, "BF-A")
    second = _scan(warehouse, "BF-A")
    third = _scan(warehouse, "BF-A")
    _entry(note, "BF-A", ReturnStatus.CANCELLED)
    _entry(note, "BF-A", ReturnStatus.CANCELLED)
    _entry(note, "BF-A", ReturnStatus.POSTED)

    assert void_dead_return_scans(connection) == 2

    for scan in (first, second, third):
        scan.refresh_from_db()
    assert first.voided_at is not None
    assert second.voided_at is not None
    # Dòng mới nhất là dòng đã sinh ra nhóm đơn còn sống — phải ở lại.
    assert third.voided_at is None


def test_voids_all_scans_when_no_live_entry(warehouse, note):
    """Huỷ lần nhập duy nhất → dòng quét là xác, biến mất hẳn."""
    scan = _scan(warehouse, "BF-B")
    _entry(note, "BF-B", ReturnStatus.CANCELLED)

    assert void_dead_return_scans(connection) == 1

    scan.refresh_from_db()
    assert scan.voided_at is not None
    # Trạng thái giữ nguyên: `voided_at` là trục riêng, không đè nghĩa của status.
    assert scan.status == PendingScanStatus.MATCHED


def test_leaves_unmatched_scans_alone(warehouse):
    """Dòng chưa khớp chưa từng ra nhóm đơn hoàn — thu hồi chúng là NUỐT MÃ."""
    kept = [
        _scan(warehouse, "BF-C1", status=PendingScanStatus.PENDING),
        _scan(warehouse, "BF-C2", status=PendingScanStatus.FAILED),
        _scan(warehouse, "BF-C3", status=PendingScanStatus.INVALID),
        _scan(warehouse, "BF-C4", status=PendingScanStatus.CANCELLED),
    ]

    assert void_dead_return_scans(connection) == 0

    for scan in kept:
        scan.refresh_from_db()
        assert scan.voided_at is None


def test_leaves_dispatch_scans_alone(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="BF-D", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    assert void_dead_return_scans(connection) == 0
    scan.refresh_from_db()
    assert scan.voided_at is None


def test_leaves_warranty_journey_scans_alone(warehouse):
    """Khớp hành trình bảo hành không sinh nhóm đơn hoàn — không có gì chết."""
    scan = _scan(warehouse, "BF-E", result_ref="warranty-journey:42")
    assert void_dead_return_scans(connection) == 0
    scan.refresh_from_db()
    assert scan.voided_at is None


def test_idempotent(warehouse, note):
    _scan(warehouse, "BF-F")
    _entry(note, "BF-F", ReturnStatus.CANCELLED)

    assert void_dead_return_scans(connection) == 1
    # Chạy lại (migration replay / rerun tay) không được đụng thêm dòng nào.
    assert void_dead_return_scans(connection) == 0
