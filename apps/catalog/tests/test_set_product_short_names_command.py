"""Lệnh `set_product_short_names` — cập nhật hàng loạt tên ngắn từ CSV."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.catalog.contracts.catalog_events import ProductSaved
from apps.catalog.models import Product
from apps.common.events import event_bus

pytestmark = pytest.mark.django_db


def _csv(tmp_path: Path, text: str) -> str:
    path = tmp_path / "short_names.csv"
    path.write_text(text, encoding="utf-8-sig")  # Excel lưu CSV kèm BOM
    return str(path)


def _run(path: str, *args: str) -> str:
    out = StringIO()
    call_command("set_product_short_names", "--file", path, *args, stdout=out)
    return out.getvalue()


def test_cap_nhat_theo_sku_va_bao_sku_khong_tim_thay(tmp_path):
    a = Product.objects.create(sku="OHI01-DEN", name="Ghế đen dài")
    b = Product.objects.create(sku="GOI01", name="Gối", short_name="Gối cũ")
    same = Product.objects.create(sku="MU01", name="Mũ", short_name="Mũ")
    Product.objects.create(sku="XOA01", name="Đã xoá", deleted_at=timezone.now())
    path = _csv(tmp_path, "SKU,Short_Name\nohi01-den,  Ghế   OHI01 Đen \nGOI01,\nMU01,Mũ\nXOA01,X\nKHONG-CO,Y\n")
    with patch.object(event_bus, "publish_on_commit") as publish:
        out = _run(path)
    published = [c.args[0] for c in publish.call_args_list if isinstance(c.args[0], ProductSaved)]

    a.refresh_from_db()
    b.refresh_from_db()
    same.refresh_from_db()
    assert a.short_name == "Ghế OHI01 Đen"  # gộp khoảng trắng thừa
    assert b.short_name == ""  # ô trống = xoá tên ngắn
    assert same.short_name == "Mũ"
    assert "Đổi 2" in out and "Không đổi 1" in out
    assert "KHONG-CO" in out and "XOA01" in out  # SKU đã xoá mềm coi như không có
    assert sorted(e.product_id for e in published) == sorted([a.id, b.id])  # chỉ SKU đổi mới phát event


def test_dry_run_khong_ghi(tmp_path):
    product = Product.objects.create(sku="OHI01-DEN", name="Ghế")
    out = _run(_csv(tmp_path, "sku,short_name\nOHI01-DEN,Ghế OHI01\n"), "--dry-run")
    product.refresh_from_db()
    assert product.short_name == ""
    assert "dry-run" in out and "Đổi 1" in out


def test_thieu_cot_hoac_qua_dai_thi_loi_va_khong_ghi(tmp_path):
    product = Product.objects.create(sku="OHI01-DEN", name="Ghế")
    with pytest.raises(CommandError, match="thiếu cột"):
        _run(_csv(tmp_path, "sku,name\nOHI01-DEN,x\n"))
    with pytest.raises(CommandError, match="> 120"):
        _run(_csv(tmp_path, f"sku,short_name\nOHI01-DEN,ok\nGOI01,{'x' * 121}\n"))
    product.refresh_from_db()
    assert product.short_name == ""
