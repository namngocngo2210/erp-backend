"""Mã chờ chưa khớp không bị UPDATE mỗi vòng cron 60s (audit 2026-09-23 T1).

Trước: mỗi vòng `match_pending_scans` ghi attempts/last_attempt_at cho MỌI mã chờ
dù không có gì đổi → 106k UPDATE/20h trên pending_shipment_scans. Sau: chỉ ghi
khi mốc thử cũ chưa có hoặc đã quá backoff.
"""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(code="WT", name="Kho throttle")


def _scan_updates(ctx) -> list[str]:
    return [
        q["sql"] for q in ctx.captured_queries
        if q["sql"].startswith("UPDATE") and "pending_shipment_scans" in q["sql"]
    ]


def test_unmatched_scan_written_once_then_throttled(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="THROTTLE-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    # Vòng 1: chưa có mốc thử → ghi.
    with CaptureQueriesContext(connection) as first:
        PendingScanService.match_pending_scans()
    assert len(_scan_updates(first)) == 1
    scan.refresh_from_db()
    assert scan.attempts == 1
    stamp = scan.last_attempt_at
    assert stamp is not None

    # Vòng 2 ngay sau: vẫn thử khớp (mã chờ không bị backoff) nhưng KHÔNG ghi.
    with CaptureQueriesContext(connection) as second:
        summary = PendingScanService.match_pending_scans()
    assert summary["unmatched"] == 1
    assert _scan_updates(second) == []
    scan.refresh_from_db()
    assert scan.attempts == 1
    assert scan.last_attempt_at == stamp
    assert scan.status == PendingScanStatus.PENDING

    # Mốc thử cũ quá backoff → ghi lại dấu lần thử.
    PendingShipmentScan.objects.filter(id=scan.id).update(
        last_attempt_at=timezone.now() - timedelta(minutes=10)
    )
    with CaptureQueriesContext(connection) as third:
        PendingScanService.match_pending_scans()
    assert len(_scan_updates(third)) == 1
    scan.refresh_from_db()
    assert scan.attempts == 2
