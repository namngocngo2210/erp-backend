"""Helper nở SKU combo ra sản phẩm con cho file Excel Lịch sử nhập xuất.

Chỉ ảnh hưởng tầng xuất báo cáo — tồn kho vẫn ghi sổ theo SKU combo.
"""
from __future__ import annotations

import pytest

from apps.inventory.application.services.combo_expansion import ComboExpander
from apps.catalog.models import Product, ProductComponent

pytestmark = pytest.mark.django_db


def _product(sku: str, name: str) -> Product:
    return Product.objects.create(name=name, sku=sku)


def test_plain_sku_passes_through_with_name():
    p = _product("SP-A", "Sản phẩm A")
    expander = ComboExpander.for_items([{"lines": [{"product_id": p.id, "sku": "SP-A", "qty": 3}]}])
    assert expander.expand([{"product_id": p.id, "sku": "SP-A", "qty": 3}]) == [
        {"sku": "SP-A", "name": "Sản phẩm A", "combo_sku": "", "qty": 3, "gross": 3}
    ]


def test_combo_expands_each_component_with_multiplied_qty():
    combo = _product("COMBO-X", "Combo X")
    a, b = _product("SP-A", "Sản phẩm A"), _product("SP-B", "Sản phẩm B")
    ProductComponent.objects.create(parent=combo, component=a, quantity=1)
    ProductComponent.objects.create(parent=combo, component=b, quantity=3)
    lines = [{"product_id": combo.id, "sku": "COMBO-X", "qty": 2, "gross": 2}]
    expander = ComboExpander.for_items([{"lines": lines}])
    assert expander.expand(lines) == [
        {"sku": "SP-A", "name": "Sản phẩm A", "combo_sku": "COMBO-X", "qty": 2, "gross": 2},
        {"sku": "SP-B", "name": "Sản phẩm B", "combo_sku": "COMBO-X", "qty": 6, "gross": 6},
    ]


def test_gross_falls_back_to_qty_when_absent():
    """lines của phiếu kho/chuyển kho/nhập hoàn không có `gross`."""
    p = _product("SP-C", "Sản phẩm C")
    expander = ComboExpander.for_items([{"lines": [{"product_id": p.id, "sku": "SP-C", "qty": 5}]}])
    assert expander.expand([{"product_id": p.id, "sku": "SP-C", "qty": 5}])[0]["gross"] == 5


def test_empty_lines_expand_to_empty_list():
    expander = ComboExpander.for_items([{"lines": []}])
    assert expander.expand([]) == []
    assert expander.expand(None) == []


def test_expand_treats_missing_qty_as_zero():
    """`qty` vắng/`None` trong `lines` không được làm `expand` nổ — khoá lại hành
    vi guard `qty = line.get("qty") or 0` (nợ review Task 3: chưa có test)."""
    p = _product("SP-NULL-QTY", "Sản phẩm thiếu SL")
    lines = [{"product_id": p.id, "sku": "SP-NULL-QTY", "qty": None}]
    expander = ComboExpander.for_items([{"lines": lines}])
    assert expander.expand(lines) == [
        {"sku": "SP-NULL-QTY", "name": "Sản phẩm thiếu SL", "combo_sku": "", "qty": 0, "gross": 0}
    ]


def test_expand_treats_missing_product_id_as_unmatched_plain_sku():
    """`product_id` vắng/`None` không được làm `expand` nổ — tra `_components`
    với key `None` phải trả `None` (không phải combo) rồi rơi vào nhánh dòng
    thường, tên rỗng vì không tra được product (nợ review Task 3)."""
    lines = [{"product_id": None, "sku": "SP-NO-PID", "qty": 4}]
    expander = ComboExpander.for_items([{"lines": lines}])
    assert expander.expand(lines) == [
        {"sku": "SP-NO-PID", "name": "", "combo_sku": "", "qty": 4, "gross": 4}
    ]


def test_for_items_runs_two_queries_regardless_of_item_count(django_assert_num_queries):
    combo = _product("COMBO-Y", "Combo Y")
    a = _product("SP-D", "Sản phẩm D")
    ProductComponent.objects.create(parent=combo, component=a, quantity=2)
    items = [
        {"lines": [{"product_id": combo.id, "sku": "COMBO-Y", "qty": i}]} for i in range(1, 21)
    ]
    with django_assert_num_queries(2):
        ComboExpander.for_items(items)


# ─────────────────────────── merge_sku_rows (chế độ Theo SKU) ───────────────────


def test_merge_sku_rows_multiplies_every_qty_column():
    combo = _product("COMBO-Z", "Combo Z")
    a = _product("SP-E", "Sản phẩm E")
    ProductComponent.objects.create(parent=combo, component=a, quantity=3)
    expander = ComboExpander.for_product_ids([combo.id])
    rows = [{"product_id": combo.id, "sku": "COMBO-Z", "name": "Combo Z", "count": 1, "net": 2, "total": 2}]
    assert expander.merge_sku_rows(rows, ("net", "total")) == [
        {"sku": "SP-E", "name": "Sản phẩm E", "count": 1, "from_combo": 6, "net": 6, "total": 6}
    ]
