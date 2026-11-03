"""Bảng khai báo thành phần combo — CHỈ phục vụ báo cáo, không đụng ghi sổ tồn kho.

Xem docs/superpowers/specs/2026-08-12-flow-history-combo-line-split-design.md
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.orders.application.services.product_component_service import (
    ProductComponentService,
)
from apps.catalog.models import Product, ProductComponent
from apps.users.tests.helpers import make_admin

pytestmark = pytest.mark.django_db


def _product(sku: str) -> Product:
    return Product.objects.create(name=f"SP {sku}", sku=sku)


def test_component_rows_are_unique_per_pair():
    combo, child = _product("COMBO-1"), _product("SP-A")
    ProductComponent.objects.create(parent=combo, component=child, quantity=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductComponent.objects.create(parent=combo, component=child, quantity=2)


def test_component_quantity_must_be_positive():
    combo, child = _product("COMBO-2"), _product("SP-B")
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductComponent.objects.create(parent=combo, component=child, quantity=0)


def test_component_cannot_be_itself():
    combo = _product("COMBO-3")
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductComponent.objects.create(parent=combo, component=combo, quantity=1)


def test_deleting_a_product_used_as_component_is_protected():
    """component dùng PROTECT: xoá cứng SP đang là thành phần combo phải bị chặn,
    nếu không file Excel cũ mất dòng mà không ai biết (xem docstring model)."""
    combo, child = _product("COMBO-4"), _product("SP-C")
    ProductComponent.objects.create(parent=combo, component=child, quantity=1)
    with pytest.raises(ProtectedError), transaction.atomic():
        child.delete()


def test_replace_writes_rows_and_returns_them():
    combo, a, b = _product("COMBO-4"), _product("SP-C"), _product("SP-D")
    rows = ProductComponentService.replace(
        combo.id, [{"component_id": a.id, "quantity": 2}, {"component_id": b.id, "quantity": 1}]
    )
    assert [(r["sku"], r["quantity"]) for r in rows] == [("SP-C", 2), ("SP-D", 1)]
    assert ProductComponent.objects.filter(parent=combo).count() == 2


def test_replace_overwrites_previous_rows():
    combo, a, b = _product("COMBO-5"), _product("SP-E"), _product("SP-F")
    ProductComponentService.replace(combo.id, [{"component_id": a.id, "quantity": 3}])
    ProductComponentService.replace(combo.id, [{"component_id": b.id, "quantity": 1}])
    assert [r["sku"] for r in ProductComponentService.list_for(combo.id)] == ["SP-F"]


def test_replace_rejects_duplicate_component():
    combo, a = _product("COMBO-6"), _product("SP-G")
    with pytest.raises(DRFValidationError):
        ProductComponentService.replace(
            combo.id, [{"component_id": a.id, "quantity": 1}, {"component_id": a.id, "quantity": 2}]
        )


def test_replace_rejects_component_that_is_itself_a_combo():
    """Chỉ một cấp: SP con không được là combo."""
    combo, inner, leaf = _product("COMBO-7"), _product("COMBO-8"), _product("SP-H")
    ProductComponentService.replace(inner.id, [{"component_id": leaf.id, "quantity": 1}])
    with pytest.raises(DRFValidationError):
        ProductComponentService.replace(combo.id, [{"component_id": inner.id, "quantity": 1}])


def test_replace_rejects_making_a_component_into_a_combo():
    """Chiều ngược lại: SP đang là thành phần của combo khác thì không được tự khai thành phần."""
    combo, child, leaf = _product("COMBO-9"), _product("SP-I"), _product("SP-J")
    ProductComponentService.replace(combo.id, [{"component_id": child.id, "quantity": 1}])
    with pytest.raises(DRFValidationError):
        ProductComponentService.replace(child.id, [{"component_id": leaf.id, "quantity": 1}])


def test_api_get_and_put_round_trip():
    combo, a = _product("COMBO-10"), _product("SP-K")
    client = APIClient()
    client.force_authenticate(user=make_admin())
    url = f"/api/orders/products/{combo.id}/components"

    resp = client.put(url, {"components": [{"component_id": a.id, "quantity": 4}]}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data == [{"component_id": a.id, "sku": "SP-K", "name": "SP SP-K", "quantity": 4}]

    assert client.get(url).data == resp.data


def test_api_requires_authentication():
    combo = _product("COMBO-11")
    assert APIClient().get(f"/api/orders/products/{combo.id}/components").status_code in (401, 403)


def test_replace_rejects_missing_component():
    combo = _product("COMBO-12")
    with pytest.raises(DRFValidationError) as exc_info:
        ProductComponentService.replace(combo.id, [{"component_id": 999999, "quantity": 1}])
    assert exc_info.value.detail == {"detail": "Không tìm thấy sản phẩm thành phần (id 999999)."}


def test_replace_rejects_soft_deleted_component_when_newly_added():
    """Thêm MỚI một sản phẩm đã xoá mềm vào combo (chưa từng là thành phần của
    combo này trước đó) vẫn phải bị chặn — chỉ nới cho ca GIỮ NGUYÊN (xem test
    `test_replace_keeps_existing_soft_deleted_component`)."""
    combo, child = _product("COMBO-13"), _product("SP-L")
    child.deleted_at = timezone.now()
    child.save(update_fields=["deleted_at"])
    with pytest.raises(DRFValidationError) as exc_info:
        ProductComponentService.replace(combo.id, [{"component_id": child.id, "quantity": 1}])
    assert exc_info.value.detail == {
        "detail": f"Không thể thêm sản phẩm đã bị xoá mềm vào combo: SP-L (id {child.id})."
    }


def test_replace_keeps_existing_soft_deleted_component():
    """Kịch bản audit: SP-B đã là thành phần của CB01, sau đó bị xoá mềm ở
    trang Sản phẩm. Người dùng mở lại dialog CB01, chỉ sửa số lượng của SP-A
    (SP-B vẫn nằm nguyên trong danh sách gửi lên vì dialog hiển thị cả thành
    phần đã xoá mềm) rồi bấm Lưu — PHẢI lưu được, không được khoá cứng combo
    chỉ vì một thành phần đã ngừng bán ở nơi khác."""
    combo, a, b = _product("CB01"), _product("SP-A"), _product("SP-B")
    ProductComponentService.replace(
        combo.id, [{"component_id": a.id, "quantity": 1}, {"component_id": b.id, "quantity": 2}]
    )
    b.deleted_at = timezone.now()
    b.save(update_fields=["deleted_at"])

    rows = ProductComponentService.replace(
        combo.id, [{"component_id": a.id, "quantity": 5}, {"component_id": b.id, "quantity": 2}]
    )

    assert {(r["sku"], r["quantity"]) for r in rows} == {("SP-A", 5), ("SP-B", 2)}


def test_replace_rejects_fractional_quantity():
    """`int(1.9)` cắt ngầm thành 1 — số lượng thập phân phải bị từ chối rõ
    ràng thay vì làm tròn im lặng."""
    combo, a = _product("COMBO-FRAC"), _product("SP-FRAC-A")
    with pytest.raises(DRFValidationError) as exc_info:
        ProductComponentService.replace(combo.id, [{"component_id": a.id, "quantity": 1.9}])
    assert exc_info.value.detail == {
        "detail": "Số lượng thành phần phải là số nguyên, không được là số thập phân."
    }


def test_replace_accepts_integer_valued_float_quantity():
    """`2.0` là số nguyên hợp lệ (chỉ phần thập phân KHÁC 0 mới bị chặn) —
    JSON không phân biệt int/float nên payload hợp lệ có thể tới dưới dạng này."""
    combo, a = _product("COMBO-INTFLOAT"), _product("SP-INTFLOAT-A")
    rows = ProductComponentService.replace(combo.id, [{"component_id": a.id, "quantity": 2.0}])
    assert rows[0]["quantity"] == 2


def test_replace_rejects_quantity_over_cap():
    """`quantity = 10**12` tràn `PositiveIntegerField` (int4) ở Postgres — phải
    bị chặn ở tầng service thành 400 rõ ràng, không rơi xuống DB thành 500."""
    combo, a = _product("COMBO-HUGE"), _product("SP-HUGE-A")
    with pytest.raises(DRFValidationError) as exc_info:
        ProductComponentService.replace(combo.id, [{"component_id": a.id, "quantity": 10**12}])
    assert "vượt quá" in exc_info.value.detail["detail"]


def test_list_for_missing_product_raises_not_found():
    with pytest.raises(DRFNotFound) as exc_info:
        ProductComponentService.list_for(999999)
    assert exc_info.value.detail == {"detail": "Không tìm thấy sản phẩm."}


def test_replace_missing_product_raises_not_found():
    with pytest.raises(DRFNotFound) as exc_info:
        ProductComponentService.replace(999999, [])
    assert exc_info.value.detail == {"detail": "Không tìm thấy sản phẩm."}


def test_api_get_missing_product_returns_404():
    client = APIClient()
    client.force_authenticate(user=make_admin())
    resp = client.get("/api/orders/products/999999/components")
    assert resp.status_code == 404
    # apps.common.exceptions.api_exception_handler bọc thêm key "error" cho mọi
    # exception bị raise (khác với Response({"detail": ...}) build tay) — chỉ
    # assert đúng "detail" gốc, không assert cả dict để khỏi phụ thuộc wrapper.
    assert resp.data["detail"] == "Không tìm thấy sản phẩm."


def test_api_put_missing_product_returns_404():
    client = APIClient()
    client.force_authenticate(user=make_admin())
    resp = client.put("/api/orders/products/999999/components", {"components": []}, format="json")
    assert resp.status_code == 404
    assert resp.data["detail"] == "Không tìm thấy sản phẩm."


def test_api_put_missing_components_key_returns_400():
    combo = _product("COMBO-14")
    client = APIClient()
    client.force_authenticate(user=make_admin())
    resp = client.put(f"/api/orders/products/{combo.id}/components", {}, format="json")
    assert resp.status_code == 400
    assert resp.data == {"detail": "Thiếu danh sách components."}


def test_api_put_rejects_non_dict_row():
    """Body kiểu {"components": [1, 2]} từng ném AttributeError -> 500."""
    combo = _product("COMBO-15")
    client = APIClient()
    client.force_authenticate(user=make_admin())
    resp = client.put(f"/api/orders/products/{combo.id}/components", {"components": [1, 2]}, format="json")
    assert resp.status_code == 400
    assert resp.data["detail"] == "Mỗi dòng thành phần phải là object có component_id/quantity."


def test_api_put_rejects_null_row():
    """Body kiểu {"components": [null]} từng ném AttributeError -> 500."""
    combo = _product("COMBO-16")
    client = APIClient()
    client.force_authenticate(user=make_admin())
    resp = client.put(f"/api/orders/products/{combo.id}/components", {"components": [None]}, format="json")
    assert resp.status_code == 400
    assert resp.data["detail"] == "Mỗi dòng thành phần phải là object có component_id/quantity."
