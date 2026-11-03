"""Canh các setting mà tem vật lý và trang public phụ thuộc vào.

`WARRANTY_QR_BASE_URL` được in lên tem giấy — sai giá trị là hỏng cả lô tem, không
sửa được sau khi in. Test này là chốt chặn để không ai vô tình revert về qcheck.vn.

Từ Task 10 (2026-09-19), các khóa này đọc từ ``apps.sysconfig`` chứ không còn ở
``django.conf.settings`` — chốt chặn "gõ thiếu dấu `/`" chuyển sang test ngay ở
use site thật (``apps/warranty_codes/tests/test_batch_export_definition.py``).
"""
from django.conf import settings

from apps.sysconfig import sysconfig


def test_qr_base_url_points_to_our_own_domain():
    base_url = sysconfig.get("WARRANTY_QR_BASE_URL")
    assert base_url == "https://delax.com.vn/sp/"
    assert "qcheck" not in base_url


def test_product_media_bucket_configured():
    assert settings.MINIO_BUCKET_PRODUCTS


def test_portal_company_settings_exist():
    for name in (
        "PORTAL_COMPANY_NAME", "PORTAL_COMPANY_ADDRESS", "PORTAL_COMPANY_TAX_CODE",
        "PORTAL_COMPANY_HOTLINE", "PORTAL_COMPANY_ZALO_URL", "PORTAL_COMPANY_FACEBOOK_URL",
    ):
        # Không raise KeyError = khóa tồn tại trong registry sysconfig.
        sysconfig.get(name)


def test_product_storage_module_exposes_expected_functions():
    from apps.orders.infrastructure import product_storage

    assert callable(product_storage.put_object)
    assert callable(product_storage.get_object_bytes)
    assert callable(product_storage.delete_object)
