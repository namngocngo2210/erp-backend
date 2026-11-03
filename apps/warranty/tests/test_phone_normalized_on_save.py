"""Audit 2026-09-23 T2: SĐT warranty chuẩn hoá khi lưu, không xoá chuỗi lạ."""

import pytest

from apps.warranty.models import (
    WarrantyClaim,
    WarrantyJourney,
    WarrantyRegistration,
    normalize_phone_keep_raw,
)


def test_valid_phone_is_canonical():
    assert normalize_phone_keep_raw(" +84 912 345 678 ") == "0912345678"


def test_invalid_phone_is_kept_stripped():
    assert normalize_phone_keep_raw("  liên hệ sau ") == "liên hệ sau"
    assert normalize_phone_keep_raw(None) == ""


@pytest.mark.django_db
def test_registration_phone_normalized_on_save():
    reg = WarrantyRegistration.objects.create(
        source=WarrantyRegistration.SOURCE_SHOPEE,
        invoice_code="INV1",
        full_name="A",
        phone="84912345678",
    )
    reg.refresh_from_db()
    assert reg.phone == "0912345678"


def test_journey_receiver_phone_normalized_on_save(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1, receiver_phone="0912.345.678")
    j.refresh_from_db()
    assert j.receiver_phone == "0912345678"
