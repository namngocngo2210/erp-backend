"""Luật nở dòng hàng combo dùng chung cho mọi UI gợi ý dòng nhập hoàn.

Trước 07/09 luật này có HAI bản sao (panel khớp tay + màn quét trả hàng); bản vá
20/08 chỉ chạm một bên nên combo `DC30+NC01` vẫn bị chặn ở màn quét. Test ở đây
khoá luật tại đúng chỗ nó sống bây giờ — xem docs/combo-stock-expansion-2026-08-20.md §8.
"""
from __future__ import annotations

import pytest

from apps.catalog.combo_lines import (
    components_by_parent,
    expand_order_lines,
    expand_order_lines_bulk,
)
from apps.catalog.models import Product, ProductComponent

pytestmark = pytest.mark.django_db


def _product(sku: str) -> Product:
    return Product.objects.create(name=f"SP {sku}", sku=sku)


def _combo(sku: str, components: list[tuple[Product, int]]) -> Product:
    parent = _product(sku)
    for component, qty in components:
        ProductComponent.objects.create(parent=parent, component=component, quantity=qty)
    return parent


def _line(product: Product, quantity: int) -> dict:
    return {"product_id": product.id, "sku": product.sku, "name": product.name, "quantity": quantity}


def test_combo_expands_into_components():
    a, b = _product("CL-A"), _product("CL-B")
    combo = _combo("CL-A+B", [(a, 1), (b, 1)])

    out = expand_order_lines([_line(combo, 1)])

    assert {r["product_id"] for r in out} == {a.id, b.id}
    assert all(r["quantity"] == 1 for r in out)


def test_component_quantity_multiplies_by_combo_count():
    part = _product("CL-PART")
    combo = _combo("CL-PACK3", [(part, 3)])

    out = expand_order_lines([_line(combo, 2)])

    assert out == [{"product_id": part.id, "sku": "CL-PART", "name": part.name, "quantity": 6}]


def test_combo_component_merges_with_standalone_line():
    """Bút toán ghi sổ gộp hai phần thành MỘT key, và `build_lines` chặn thẳng hai
    dòng trùng (product_id, return_type) → ở đây phải gộp."""
    part = _product("CL-MERGE")
    combo = _combo("CL-MERGE-COMBO", [(part, 1)])

    out = expand_order_lines([_line(combo, 1), _line(part, 2)])

    assert len(out) == 1
    assert out[0]["product_id"] == part.id
    assert out[0]["quantity"] == 3


def test_two_combos_sharing_a_component_merge():
    shared = _product("CL-SHARED")
    only_a = _product("CL-ONLY-A")
    combo_a = _combo("CL-COMBO-A", [(shared, 1), (only_a, 1)])
    combo_b = _combo("CL-COMBO-B", [(shared, 2)])

    out = {r["product_id"]: r["quantity"] for r in expand_order_lines([_line(combo_a, 1), _line(combo_b, 1)])}

    assert out == {shared.id: 3, only_a.id: 1}


def test_combo_without_declared_components_stays_put():
    """Fallback giống `expand_desired_quantities`: phần lớn SKU dạng combo trên prod
    chưa khai thành phần, bỏ qua chúng sẽ đánh mất dòng hàng."""
    combo = _product("CL-NODECL")

    out = expand_order_lines([_line(combo, 2)])

    assert out == [{"product_id": combo.id, "sku": "CL-NODECL", "name": combo.name, "quantity": 2}]


def test_plain_product_is_untouched():
    plain = _product("CL-PLAIN")

    assert expand_order_lines([_line(plain, 1)]) == [_line(plain, 1)]


def test_lines_without_product_pass_through_ungrouped():
    """Dòng chưa khớp sản phẩm không có gì để đối chiếu — gộp chúng lại là trộn hai
    mặt hàng khác nhau thành một."""
    rows = [
        {"product_id": None, "sku": "", "name": "Hàng lạ 1", "quantity": 1},
        {"product_id": None, "sku": "", "name": "Hàng lạ 2", "quantity": 2},
    ]

    assert expand_order_lines(rows) == rows


def test_with_name_false_omits_name_key():
    """`flow_order_lookup` không khai `name` trong serializer của nó."""
    part = _product("CL-NONAME")
    combo = _combo("CL-NONAME-COMBO", [(part, 1)])

    out = expand_order_lines(
        [{"product_id": combo.id, "sku": combo.sku, "quantity": 1}], with_name=False
    )

    assert out == [{"product_id": part.id, "sku": "CL-NONAME", "quantity": 1}]


def test_bulk_expands_each_order_independently():
    part = _product("CL-BULK")
    combo = _combo("CL-BULK-COMBO", [(part, 1)])

    out = expand_order_lines_bulk({7: [_line(combo, 1)], 9: [_line(part, 5)]})

    assert out[7] == [{"product_id": part.id, "sku": "CL-BULK", "name": part.name, "quantity": 1}]
    assert out[9] == [_line(part, 5)]


def test_bulk_reads_components_in_a_single_query(django_assert_num_queries):
    """Màn tra cứu trả cả trang kết quả — nở phải là 1 query, không N+1."""
    part = _product("CL-Q")
    combo = _combo("CL-Q-COMBO", [(part, 1)])
    rows_by_order = {i: [_line(combo, 1)] for i in range(10)}

    with django_assert_num_queries(1):
        expand_order_lines_bulk(rows_by_order)


def test_components_by_parent_skips_non_combo_and_empty_input():
    plain = _product("CL-NONE")

    assert components_by_parent([plain.id]) == {}
    assert components_by_parent([]) == {}
    assert components_by_parent([None]) == {}
