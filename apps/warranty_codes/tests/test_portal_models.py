import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import (
    PortalChannel,
    ScanResult,
    WarrantyActivation,
    WarrantyCode,
    WarrantyCodeScanLog,
)

pytestmark = pytest.mark.django_db


def _one_code() -> WarrantyCode:
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    return WarrantyCode.objects.filter(batch=batch).first()


def test_activation_is_unique_per_code():
    code = _one_code()
    now = timezone.now()
    WarrantyActivation.objects.create(
        code=code, phone="0901234567", channel=PortalChannel.WEB,
        activated_at=now, expires_at=now.date(),
    )
    with pytest.raises(IntegrityError):
        WarrantyActivation.objects.create(
            code=code, phone="0909999999", channel=PortalChannel.WEB,
            activated_at=now, expires_at=now.date(),
        )


def test_scan_log_allows_null_code_for_unknown_keyword():
    log = WarrantyCodeScanLog.objects.create(
        keyword="KHONGCO", code=None, result=ScanResult.NOT_FOUND,
        channel=PortalChannel.WEB, ip_address="1.2.3.4", user_agent="UA",
    )
    assert log.id is not None
    assert log.code_id is None


def test_scan_log_keeps_row_when_code_deleted():
    code = _one_code()
    WarrantyCodeScanLog.objects.create(
        keyword=code.code, code=code, result=ScanResult.ACTIVATED, channel=PortalChannel.WEB,
    )
    WarrantyCode.objects.filter(id=code.id).delete()
    log = WarrantyCodeScanLog.objects.get(keyword=code.code)
    assert log.code_id is None


def test_warranty_code_has_scan_counter_defaults():
    code = _one_code()

    assert code.scan_count == 0
    assert code.first_scanned_at is None


def test_scan_result_has_page_viewed():
    assert ScanResult.PAGE_VIEWED == "page_viewed"
    assert ScanResult.PAGE_VIEWED.label == "Xem trang sản phẩm"
