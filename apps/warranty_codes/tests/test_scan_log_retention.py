from datetime import timedelta

import pytest
from django.utils import timezone

from apps.warranty_codes.models import ScanResult, WarrantyCodeScanLog
from apps.warranty_codes.tasks import purge_old_scan_logs

pytestmark = pytest.mark.django_db


def test_purge_removes_only_old_rows(settings):
    settings.WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS = 180
    old = WarrantyCodeScanLog.objects.create(keyword="OLD", result=ScanResult.NOT_FOUND, channel="web")
    fresh = WarrantyCodeScanLog.objects.create(keyword="NEW", result=ScanResult.NOT_FOUND, channel="web")
    WarrantyCodeScanLog.objects.filter(id=old.id).update(
        created_at=timezone.now() - timedelta(days=200)
    )
    deleted = purge_old_scan_logs()
    assert deleted == 1
    assert list(WarrantyCodeScanLog.objects.values_list("id", flat=True)) == [fresh.id]


def test_purge_disabled_when_retention_zero(settings):
    settings.WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS = 0
    WarrantyCodeScanLog.objects.create(keyword="X", result=ScanResult.NOT_FOUND, channel="web")
    assert purge_old_scan_logs() == 0
    assert WarrantyCodeScanLog.objects.count() == 1
