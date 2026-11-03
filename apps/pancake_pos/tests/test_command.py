"""Lệnh `pancake_pos_sync` + factory: cờ ENABLED chỉ chặn lượt ghi, dry-run in tóm tắt."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from apps.catalog.models import Product
from apps.pancake_pos.application.services.factory import PancakePosDisabled, build_product_sync_service
from apps.pancake_pos.application.services.product_sync_service import ProductSyncService
from apps.pancake_pos.domain.entities import CatalogProduct
from apps.pancake_pos.tests.fakes import FakePancake, FakeReader
from apps.sysconfig import override_sysconfig

pytestmark = pytest.mark.django_db
FACTORY = "apps.pancake_pos.management.commands.pancake_pos_sync.build_product_sync_service"


def test_factory_chan_luot_ghi_khi_tat():
    with override_sysconfig(PANCAKE_POS_ENABLED=False, PANCAKE_POS_API_KEY="k", PANCAKE_POS_SHOP_ID=1):
        with pytest.raises(PancakePosDisabled):
            build_product_sync_service(dry_run=False)
        assert isinstance(build_product_sync_service(dry_run=True), ProductSyncService)


def test_lenh_bao_loi_khi_thieu_key():
    with override_sysconfig(PANCAKE_POS_API_KEY="", PANCAKE_POS_SHOP_ID=1), pytest.raises(CommandError):
        call_command("pancake_pos_sync", "--dry-run")


def test_lenh_dry_run_in_tom_tat():
    product = Product.objects.create(sku="GOI01", name="Gối")
    pancake = FakePancake()
    service = ProductSyncService(
        gateway=pancake,
        catalog_reader=FakeReader([CatalogProduct(id=product.id, sku="GOI01", name="Gối", has_stock=True)]),
    )
    out = StringIO()
    with patch(FACTORY, return_value=service) as factory:
        call_command("pancake_pos_sync", "--dry-run", "--sku", "GOI01", "--limit", "5", stdout=out)
    factory.assert_called_once_with(dry_run=True)
    text = out.getvalue()
    assert "Tạo 1" in text and "dry-run" in text
    assert pancake.write_calls() == []
