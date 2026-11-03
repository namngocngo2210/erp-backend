# Lịch sử nhập xuất: tách dòng theo mã hàng + nổ SKU combo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Ghi chú cập nhật (13/08/2026 — sau khi thực thi xong):** Plan này là kế
> hoạch đã thực thi xong, giữ nguyên bên dưới (kể cả code mẫu trong các Step)
> để lưu lịch sử triển khai. Hai điểm sau đã đổi khác trong lúc thực thi so
> với những gì các Step mô tả (đặc biệt Task 4, Task 5, Task 6 — chỗ code mẫu
> còn ghi `Tổng SL sau nổ combo` và nhánh Theo SKU dừng ở `Tổng cộng (SKU ghi
> sổ)` không có dòng tổng cuối):
>
> 1. **Đổi tên dòng tổng cuối** thành `Tổng SL các dòng trong file`. Tên cũ
>    `Tổng SL sau nổ combo` ngụ ý sai rằng chênh lệch với các dòng "... (SKU
>    ghi sổ)" chỉ đến từ việc nổ combo; thực tế còn lệch do cách tính SL từng
>    dòng dùng số đã từng xuất/hoàn TRƯỚC khi đảo sổ, khác với dòng ghi sổ chỉ
>    tính phần còn thật sự ở ngoài kho.
> 2. **Chế độ Theo SKU (Task 6) cũng có dòng tổng cuối này**, không dừng ở
>    `Tổng cộng (SKU ghi sổ)` như code mẫu ở Step 4/Step 5. Thực thi nhận ra
>    chế độ Theo SKU mới là nơi cần dòng đối chiếu này nhất vì mọi dòng ở chế
>    độ này đều đã nở/gộp combo.

**Goal:** File Excel của trang Lịch sử nhập xuất tách mỗi mã hàng thành một dòng riêng, và nổ SKU combo ra các sản phẩm con với số lượng đã nhân.

**Architecture:** Thêm bảng khai báo thành phần combo (`product_components`) ở app `orders` với API + dialog trên màn Sản phẩm. Ở app `inventory`, một helper dùng chung `combo_expansion.py` nạp tên sản phẩm và bảng thành phần bằng query gộp, rồi nở `lines` của mỗi đơn/phiếu thành các dòng phẳng. Hai `ExportDefinition` (`dispatch_history`, `return_history`) gọi helper đó. Tồn kho không đổi.

**Tech Stack:** Django 6.0.7 + DRF (function-based views), PostgreSQL, pytest; React + TypeScript + shadcn/ui, vitest.

**Spec:** `docs/superpowers/specs/2026-08-12-flow-history-combo-line-split-design.md`

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order-combo-line-split`, nhánh `feat/flow-history-combo-line-split`.
- Django 6.0.7 → `CheckConstraint(condition=...)`, **không** phải `check=`.
- Lint bắt buộc: `ruff check .` theo `backend/pyproject.toml` phải sạch.
- Không có CI trên master — mọi lint/test phải chạy tay.
- Comment và message hướng người dùng viết tiếng Việt (bám codebase); commit message tiếng Anh.
- Bảng `product_components` **không được đọc ở bất kỳ đâu trong luồng ghi `StockMovement`**.
- `backend/schema.yaml` lệch sẵn ~444 dòng trên master → **sửa tay** phần của mình, không regen ghi đè.
- Hook prettier của repo tự format file FE khi commit; chỉ `git add` đúng file mình sửa để tránh nhiễu.

### Môi trường test (làm một lần trước Task 1)

Container test đã bị xoá trước đây; dựng lại nếu chưa có:

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

Lệnh chạy pytest dùng xuyên suốt plan này (`<paths>` thay theo từng task):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-combo-line-split && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_combo_split \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lint backend:

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Test frontend (worktree anh em → dùng junction node_modules được):

```bash
cd frontend && npm run test -- <path>
```

---

## File Structure

**Tạo mới:**

| File | Trách nhiệm |
| --- | --- |
| `backend/apps/orders/migrations/0121_product_components.py` | Migration bảng mới |
| `backend/apps/orders/application/services/product_component_service.py` | Đọc/ghi + validate BOM |
| `backend/apps/orders/transport/views/order_views_impl/product_component_views.py` | 2 endpoint GET/PUT |
| `backend/apps/orders/tests/test_product_components.py` | Test model + service + API |
| `backend/apps/inventory/application/services/combo_expansion.py` | Nạp tên SP + BOM, nở `lines` |
| `backend/apps/inventory/tests/test_combo_expansion.py` | Test helper |
| `frontend/src/lib/api/product-components.ts` | API client |
| `frontend/src/features/products/components/ComboComponentsDialog.tsx` | Dialog khai thành phần |
| `frontend/src/features/products/__tests__/combo-components-dialog.test.tsx` | Test dialog |

**Sửa:**

| File | Thay đổi |
| --- | --- |
| `backend/apps/orders/models.py` | Thêm model `ProductComponent` |
| `backend/apps/orders/urls.py` | 1 route mới |
| `backend/schema.yaml` | 2 endpoint + 2 schema mới (sửa tay) |
| `backend/apps/inventory/exports_definitions.py` | Cả 4 nhánh write (2 tab × 2 chế độ) |
| `backend/apps/inventory/infrastructure/flow_history_excel.py` | Xoá 4 hàm chết + `_lines_text` |
| `backend/apps/inventory/tests/test_flow_history_excel.py` | Xoá test của 4 hàm chết |
| `backend/apps/inventory/tests/test_inventory_export_definitions.py` | Bổ sung test tách dòng |
| `frontend/src/api/schema.d.ts` | Áp hunk type mới |
| `frontend/src/pages/ProductsPage.tsx` | Nút mở dialog (desktop + mobile) |

---

### Task 1: Model `ProductComponent` + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm sau `ProductPackage`, khoảng dòng 221)
- Create: `backend/apps/orders/migrations/0121_product_components.py` (sinh bằng makemigrations)
- Test: `backend/apps/orders/tests/test_product_components.py`

**Interfaces:**
- Produces: `apps.orders.models.ProductComponent` với field `parent` (FK Product, related_name `components`), `component` (FK Product, related_name `in_combos`), `quantity` (PositiveIntegerField).

- [ ] **Step 1: Write the failing test**

Tạo `backend/apps/orders/tests/test_product_components.py`:

```python
"""Bảng khai báo thành phần combo — CHỈ phục vụ báo cáo, không đụng ghi sổ tồn kho.

Xem docs/superpowers/specs/2026-08-12-flow-history-combo-line-split-design.md
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.orders.models import Product, ProductComponent

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: lệnh pytest ở Global Constraints với `<paths>` = `apps/orders/tests/test_product_components.py`
Expected: FAIL — `ImportError: cannot import name 'ProductComponent'`

- [ ] **Step 3: Thêm model**

Trong `backend/apps/orders/models.py`, chèn ngay sau class `ProductPackage` (trước `class PriceBook`):

```python
class ProductComponent(models.Model):
    """Thành phần của một SKU combo — CHỈ phục vụ báo cáo, KHÔNG tham gia ghi sổ tồn kho.

    Tồn kho vẫn trừ theo chính SKU combo như trước; bảng này chỉ để file Excel
    Lịch sử nhập xuất nở combo ra các sản phẩm con. Cố tình KHÔNG có cờ
    ``is_combo`` trên ``Product``: có ≥1 dòng ở đây nghĩa là combo, một nguồn sự
    thật duy nhất.

    Chỉ MỘT cấp — sản phẩm con không được tự nó là combo (chặn ở
    ``product_component_service``, hai chiều).
    """

    parent = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="components")
    # PROTECT: xoá cứng một SP đang là thành phần combo sẽ làm file Excel cũ mất
    # dòng mà không ai biết.
    component = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="in_combos")
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_components"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "component"], name="uniq_product_component"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="product_component_qty_positive"
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("component")), name="product_component_not_self"
            ),
        ]
        indexes = [
            # `parent` đã có index nhờ UniqueConstraint(parent, component) — chỉ
            # cần thêm chiều ngược "SKU này nằm trong những combo nào".
            models.Index(fields=["component"], name="prodcomp_component_idx"),
        ]
```

- [ ] **Step 4: Sinh migration**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-combo-line-split && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_combo_split \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations orders
```

Expected: tạo `apps/orders/migrations/0121_product_components.py`. Mở file, xác nhận không có thay đổi lạ nào ngoài bảng mới (nếu có, master đang lệch model — dừng lại hỏi).

- [ ] **Step 5: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/orders/tests/test_product_components.py`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0121_product_components.py backend/apps/orders/tests/test_product_components.py
git commit -m "feat(products): add product_components table for combo BOM"
```

---

### Task 2: Service + API khai báo thành phần

**Files:**
- Create: `backend/apps/orders/application/services/product_component_service.py`
- Create: `backend/apps/orders/transport/views/order_views_impl/product_component_views.py`
- Modify: `backend/apps/orders/urls.py`
- Modify: `backend/schema.yaml`
- Test: `backend/apps/orders/tests/test_product_components.py` (thêm vào file Task 1)

**Interfaces:**
- Consumes: `ProductComponent` (Task 1).
- Produces:
  - `ProductComponentService.list_for(product_id: int) -> list[dict]` với key `component_id`, `sku`, `name`, `quantity`
  - `ProductComponentService.replace(product_id: int, rows: list[dict]) -> list[dict]` — `rows` mỗi phần tử `{"component_id": int, "quantity": int}`; ném `rest_framework.exceptions.ValidationError` khi dữ liệu sai
  - Route `GET|PUT /api/orders/products/<int:product_id>/components`

- [ ] **Step 1: Write the failing test**

Thêm các import sau vào **khối import đầu file** (ruff E402 cấm import giữa file):

```python
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.orders.application.services.product_component_service import (
    ProductComponentService,
)
from apps.users.test_helpers import make_admin
```

Rồi thêm các test sau vào cuối `backend/apps/orders/tests/test_product_components.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: pytest với `<paths>` = `apps/orders/tests/test_product_components.py`
Expected: FAIL — `ModuleNotFoundError: apps.orders.application.services.product_component_service`

- [ ] **Step 3: Viết service**

Tạo `backend/apps/orders/application/services/product_component_service.py`:

```python
"""Khai báo thành phần của SKU combo.

CHỈ phục vụ báo cáo (file Excel Lịch sử nhập xuất nở combo ra sản phẩm con).
Không có code nào trong luồng ghi ``StockMovement`` đọc bảng này.
"""
from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.orders.models import Product, ProductComponent


class ProductComponentService:
    @staticmethod
    def list_for(product_id: int) -> list[dict]:
        return [
            {
                "component_id": r["component_id"],
                "sku": r["component__sku"],
                "name": r["component__name"],
                "quantity": r["quantity"],
            }
            for r in ProductComponent.objects.filter(parent_id=product_id)
            .values("component_id", "component__sku", "component__name", "quantity")
            .order_by("component__sku")
        ]

    @staticmethod
    def _validate(product_id: int, rows: list[dict]) -> list[tuple[int, int]]:
        cleaned: list[tuple[int, int]] = []
        seen: set[int] = set()
        for row in rows:
            try:
                component_id = int(row.get("component_id"))
                quantity = int(row.get("quantity"))
            except (TypeError, ValueError):
                raise ValidationError({"detail": "component_id/quantity phải là số nguyên."}) from None
            if quantity < 1:
                raise ValidationError({"detail": "Số lượng thành phần phải từ 1 trở lên."})
            if component_id == product_id:
                raise ValidationError({"detail": "Combo không thể chứa chính nó."})
            if component_id in seen:
                raise ValidationError({"detail": "Một sản phẩm chỉ được khai một lần trong combo."})
            seen.add(component_id)
            cleaned.append((component_id, quantity))

        if not cleaned:
            return cleaned

        component_ids = [c for c, _ in cleaned]
        existing = set(
            Product.objects.filter(id__in=component_ids, deleted_at__isnull=True).values_list("id", flat=True)
        )
        missing = [c for c in component_ids if c not in existing]
        if missing:
            raise ValidationError({"detail": "Sản phẩm thành phần không tồn tại."})

        # Chỉ MỘT cấp, chặn cả hai chiều: thành phần không được là combo, và một
        # SP đang là thành phần của combo khác thì không được tự khai thành phần.
        if ProductComponent.objects.filter(parent_id__in=component_ids).exists():
            raise ValidationError({"detail": "Sản phẩm thành phần không được là một combo khác."})
        if ProductComponent.objects.filter(component_id=product_id).exists():
            raise ValidationError(
                {"detail": "Sản phẩm này đang là thành phần của combo khác nên không thể tự khai thành phần."}
            )
        return cleaned

    @staticmethod
    @transaction.atomic
    def replace(product_id: int, rows: list[dict]) -> list[dict]:
        """Ghi đè trọn danh sách thành phần của một combo."""
        if not Product.objects.filter(id=product_id, deleted_at__isnull=True).exists():
            raise ValidationError({"detail": "Không tìm thấy sản phẩm."})
        cleaned = ProductComponentService._validate(product_id, rows)
        ProductComponent.objects.filter(parent_id=product_id).delete()
        ProductComponent.objects.bulk_create(
            [
                ProductComponent(parent_id=product_id, component_id=component_id, quantity=quantity)
                for component_id, quantity in cleaned
            ]
        )
        return ProductComponentService.list_for(product_id)
```

- [ ] **Step 4: Viết view**

Tạo `backend/apps/orders/transport/views/order_views_impl/product_component_views.py`:

```python
"""2 endpoint khai báo thành phần combo — bám khuôn function-based view của
``product_catalog_views`` (cùng quyền, cùng ``never_cache``)."""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.product_component_service import (
    ProductComponentService,
)
from apps.users.permissions import ORDER_SECTIONS, require_section

_ROW = inline_serializer(
    "ProductComponentRow",
    fields={
        "component_id": drf_serializers.IntegerField(),
        "sku": drf_serializers.CharField(),
        "name": drf_serializers.CharField(),
        "quantity": drf_serializers.IntegerField(),
    },
)


@extend_schema(
    request=inline_serializer(
        "ProductComponentsRequest",
        fields={
            "components": inline_serializer(
                "ProductComponentInput",
                many=True,
                fields={
                    "component_id": drf_serializers.IntegerField(),
                    "quantity": drf_serializers.IntegerField(),
                },
            ),
        },
    ),
    responses={
        200: _ROW,
        400: OpenApiResponse(description="Dữ liệu thành phần không hợp lệ"),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
@never_cache
def product_components(request, product_id: int):
    """Đọc / ghi đè danh sách thành phần của một SKU combo.

    PUT ghi đè TRỌN danh sách (không PATCH từng dòng): người dùng sửa cả bảng
    trong một dialog rồi bấm Lưu, ghi đè tránh trạng thái nửa vời.
    """
    if request.method == "GET":
        return Response(ProductComponentService.list_for(product_id), status=status.HTTP_200_OK)
    rows = request.data.get("components")
    if not isinstance(rows, list):
        return Response({"detail": "Thiếu danh sách components."}, status=status.HTTP_400_BAD_REQUEST)
    return Response(ProductComponentService.replace(product_id, rows), status=status.HTTP_200_OK)
```

- [ ] **Step 5: Đăng ký route**

Trong `backend/apps/orders/urls.py`, thêm import và route ngay sau dòng `path("products/<int:product_id>", update_delete_product, ...)`:

```python
from apps.orders.transport.views.order_views_impl.product_component_views import (
    product_components,
)
```

```python
    path(
        "products/<int:product_id>/components",
        product_components,
        name="orders-product-components",
    ),
```

- [ ] **Step 6: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/orders/tests/test_product_components.py`
Expected: 10 passed

- [ ] **Step 7: Sửa tay `schema.yaml`**

`backend/schema.yaml` lệch sẵn ~444 dòng trên master → **không regen ghi đè**. Regen ra file phụ rồi chỉ chép khối của mình. Ghi ra `/app/schema-new.yaml` (nằm trong bind-mount) để lấy được file ra ngoài container:

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-combo-line-split && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_combo_split \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py spectacular --file /app/schema-new.yaml
```

Đối chiếu hai file để lấy đúng phần mới:

```bash
diff backend/schema.yaml backend/schema-new.yaml | grep -i "component" | head -40
```

Chép vào `backend/schema.yaml` **đúng vị trí alphabet**:
- path `/api/orders/products/{product_id}/components`
- các schema `ProductComponentRow`, `ProductComponentsRequest`, `ProductComponentInput`

Rồi `rm backend/schema-new.yaml`. Ba bẫy đã cắn ở MR trước: `required` sort theo alphabet **tên field** (không theo thứ tự khai trong serializer); `ChoiceField` bị hoist thành schema enum riêng rồi `$ref` tới (task này không có ChoiceField nên không dính); không param nào trong file có `explode`/`style` — thêm vào là lệch.

- [ ] **Step 8: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/application/services/product_component_service.py \
        backend/apps/orders/transport/views/order_views_impl/product_component_views.py \
        backend/apps/orders/urls.py backend/apps/orders/tests/test_product_components.py backend/schema.yaml
git commit -m "feat(products): api to read and replace combo components"
```

---

### Task 3: Helper nổ combo

**Files:**
- Create: `backend/apps/inventory/application/services/combo_expansion.py`
- Test: `backend/apps/inventory/tests/test_combo_expansion.py`

**Interfaces:**
- Consumes: `ProductComponent` (Task 1).
- Produces:
  - `ComboExpander.for_items(items: list[dict]) -> ComboExpander` — `items` là list dict có key `lines`
  - `ComboExpander.expand(lines: list[dict] | None) -> list[dict]` — mỗi phần tử có `sku: str`, `name: str`, `combo_sku: str`, `qty: int`, `gross: int`
  - `ComboExpander.for_product_ids(product_ids) -> ComboExpander`
  - `ComboExpander.merge_sku_rows(rows: list[dict], qty_keys: tuple[str, ...]) -> list[dict]` — thêm ở Task 6

Ghi chú shape đầu vào (đã đối chiếu code): mọi `lines` đều có `product_id`, `sku`, `qty`; riêng lines của đơn ở tab Xuất có thêm `gross`.

- [ ] **Step 1: Write the failing test**

Tạo `backend/apps/inventory/tests/test_combo_expansion.py`:

```python
"""Helper nở SKU combo ra sản phẩm con cho file Excel Lịch sử nhập xuất.

Chỉ ảnh hưởng tầng xuất báo cáo — tồn kho vẫn ghi sổ theo SKU combo.
"""
from __future__ import annotations

import pytest

from apps.inventory.application.services.combo_expansion import ComboExpander
from apps.orders.models import Product, ProductComponent

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


def test_for_items_runs_two_queries_regardless_of_item_count(django_assert_num_queries):
    combo = _product("COMBO-Y", "Combo Y")
    a = _product("SP-D", "Sản phẩm D")
    ProductComponent.objects.create(parent=combo, component=a, quantity=2)
    items = [
        {"lines": [{"product_id": combo.id, "sku": "COMBO-Y", "qty": i}]} for i in range(1, 21)
    ]
    with django_assert_num_queries(2):
        ComboExpander.for_items(items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: pytest với `<paths>` = `apps/inventory/tests/test_combo_expansion.py`
Expected: FAIL — `ModuleNotFoundError: ...combo_expansion`

- [ ] **Step 3: Viết helper**

Tạo `backend/apps/inventory/application/services/combo_expansion.py`:

```python
"""Nở SKU combo ra sản phẩm con cho file Excel Lịch sử nhập xuất.

MỘT chỗ duy nhất dùng chung cho cả tab Xuất và tab Nhập hoàn: màn này từng có
hai nhánh code song song (màn hình và job export nền) khiến sửa một bên là lệch
số — helper chung là cách chặn việc đó tái diễn.

Tồn kho KHÔNG đổi: vẫn ghi sổ theo chính SKU combo. Việc nở chỉ xảy ra ở tầng
xuất báo cáo, theo khai báo HIỆN HÀNH trong ``product_components`` (bảng không
có phiên bản — sửa thành phần hôm nay thì file của kỳ trước cũng đổi số).
"""
from __future__ import annotations

from apps.orders.models import Product, ProductComponent


class ComboExpander:
    """Giữ sẵn tên sản phẩm + bảng thành phần cho MỘT trang dữ liệu."""

    __slots__ = ("_names", "_components")

    def __init__(self, names: dict[int, str], components: dict[int, list[dict]]) -> None:
        self._names = names
        self._components = components

    @staticmethod
    def for_items(items: list[dict]) -> "ComboExpander":
        """Nạp dữ liệu cho mọi `product_id` xuất hiện trong trang — đúng 2 query."""
        product_ids = {
            line.get("product_id")
            for item in (items or [])
            for line in (item.get("lines") or [])
            if line.get("product_id")
        }
        return ComboExpander.for_product_ids(product_ids)

    @staticmethod
    def for_product_ids(product_ids) -> "ComboExpander":
        product_ids = set(product_ids or ())
        components: dict[int, list[dict]] = {}
        if product_ids:
            for r in (
                ProductComponent.objects.filter(parent_id__in=product_ids)
                .values("parent_id", "component_id", "component__sku", "component__name", "quantity")
                .order_by("component__sku")
            ):
                components.setdefault(r["parent_id"], []).append(
                    {
                        "product_id": r["component_id"],
                        "sku": r["component__sku"] or "",
                        "name": r["component__name"] or "",
                        "quantity": r["quantity"],
                    }
                )
        # Tên sản phẩm cho CẢ sản phẩm gốc lẫn sản phẩm con (dòng nở lấy tên con).
        names_needed = set(product_ids)
        names: dict[int, str] = {}
        if names_needed:
            names = {
                r["id"]: r["name"] or ""
                for r in Product.objects.filter(id__in=names_needed).values("id", "name")
            }
        return ComboExpander(names, components)

    def is_combo(self, product_id) -> bool:
        return bool(self._components.get(product_id))

    def components_of(self, product_id) -> list[dict]:
        return self._components.get(product_id) or []

    def expand(self, lines: list[dict] | None) -> list[dict]:
        """Nở một danh sách `lines` thành các dòng phẳng đã nhân số lượng.

        SKU thường giữ nguyên một dòng, ``combo_sku`` rỗng. ``gross`` (SL đã
        TỪNG rời kho, chỉ tab Xuất có) mặc định bằng ``qty`` cho các luồng không
        khai field này.
        """
        out: list[dict] = []
        for line in lines or []:
            product_id = line.get("product_id")
            qty = line.get("qty") or 0
            gross = line.get("gross")
            gross = qty if gross is None else gross
            comps = self._components.get(product_id)
            if not comps:
                out.append(
                    {
                        "sku": line.get("sku") or "",
                        "name": self._names.get(product_id, ""),
                        "combo_sku": "",
                        "qty": qty,
                        "gross": gross,
                    }
                )
                continue
            combo_sku = line.get("sku") or ""
            for c in comps:
                out.append(
                    {
                        "sku": c["sku"],
                        "name": c["name"],
                        "combo_sku": combo_sku,
                        "qty": qty * c["quantity"],
                        "gross": gross * c["quantity"],
                    }
                )
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/inventory/tests/test_combo_expansion.py`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/combo_expansion.py backend/apps/inventory/tests/test_combo_expansion.py
git commit -m "feat(inventory): add combo expander for flow history exports"
```

---

### Task 4: Tab Xuất — tách dòng theo mã hàng (chế độ Theo đơn)

**Files:**
- Modify: `backend/apps/inventory/exports_definitions.py:235-348` (`DispatchHistoryExport.write`, nhánh `group != "sku"`)
- Test: `backend/apps/inventory/tests/test_inventory_export_definitions.py`

**Interfaces:**
- Consumes: `ComboExpander.for_items`, `ComboExpander.expand` (Task 3); `_display_qty` sẵn có trong `flow_history_excel`.
- Produces: bố cục header tab Xuất chế độ Theo đơn:
  `["Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kho", "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Ghi chú", "Số SKU", "SL xuất"]`

- [ ] **Step 1: Write the failing test**

Thêm vào `backend/apps/inventory/tests/test_inventory_export_definitions.py`. Trước hết hai helper dựng dữ liệu, đặt ngay sau `_raw_filters` (dòng ~266) — cố ý **không** dùng `_seed_flow()` vì nó kèm nhập hoàn + mã chờ khớp làm nhiễu assert tách dòng:

```python
def _seed_two_skus():
    """Một đơn đã xuất kho gồm SP-A ×2 và SP-B ×1."""
    channel = Channel.objects.create(code="lp-split", name="SPLIT")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000022", phone_normalized="0900000022"
    )
    wh = Warehouse.objects.create(code="WH-SPLIT", name="Kho Split")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    b = Product.objects.create(sku="SP-B", name="Sản phẩm B")
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000022",
        order_code="DH-SPLIT", invoice_code="HD-SPLIT", shipping_code="VD-SPLIT",
        total_amount="1000",
    )
    for product, qty in ((a, 2), (b, 1)):
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=product.id, qty_delta=-qty,
            movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
        )
    return wh, order, a, b


def _seed_combo(sku_suffix: str):
    """COMBO-X = SP-A ×1 + SP-B ×2; trả (kho, đơn, combo) — CHƯA ghi movement nào."""
    channel = Channel.objects.create(code=f"lp-combo-{sku_suffix}", name="COMBO")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000033", phone_normalized="0900000033"
    )
    wh = Warehouse.objects.create(code=f"WH-CB-{sku_suffix}", name="Kho Combo")
    combo = Product.objects.create(sku="COMBO-X", name="Combo X")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    b = Product.objects.create(sku="SP-B", name="Sản phẩm B")
    ProductComponent.objects.create(parent=combo, component=a, quantity=1)
    ProductComponent.objects.create(parent=combo, component=b, quantity=2)
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000033",
        order_code=f"DH-CB-{sku_suffix}", invoice_code=f"HD-CB-{sku_suffix}",
        shipping_code=f"VD-CB-{sku_suffix}", total_amount="1000",
    )
    return wh, order, combo


def _seed_combo_dispatch():
    """Đơn xuất kho 2 combo."""
    wh, order, combo = _seed_combo("D")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    return wh, order, combo
```

Thêm `ProductComponent` vào dòng import `from apps.orders.models import Order, Product`.

Rồi các test (đặt cuối file):

```python
def test_dispatch_export_splits_one_row_per_sku():
    """Đơn 2 mã hàng → 2 dòng, mỗi dòng mang SL của riêng mã đó."""
    _seed_two_skus()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))

    assert writer.headers == [
        "Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kho", "Người thực hiện",
        "SKU", "Tên sản phẩm", "Thuộc combo", "Ghi chú", "Số SKU", "SL xuất",
    ]
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[5], r[10]) for r in detail] == [("SP-A", 2), ("SP-B", 1)]
    # Các cột đầu lặp nguyên giá trị để lọc/pivot được trên Excel.
    assert detail[0][1] == detail[1][1]


def test_dispatch_export_expands_combo_rows():
    """2 combo, combo gồm A×1 và B×2 → A = 2, B = 4; cột Thuộc combo ghi SKU gốc."""
    _seed_combo_dispatch()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[5], r[7], r[10]) for r in detail] == [
        ("SP-A", "COMBO-X", 2),
        ("SP-B", "COMBO-X", 4),
    ]


def test_dispatch_export_adds_expanded_total_row():
    """Footer ghi sổ giữ nguyên; thêm dòng tổng sau nổ combo bằng tổng cột."""
    _seed_combo_dispatch()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    last = writer.rows[-1]
    assert last[0] == "Tổng SL sau nổ combo"
    assert last[10] == sum(r[10] for r in detail)
    labels = [r[0] for r in writer.rows if str(r[0]).startswith("Tổng")]
    assert labels[:4] == [
        "Tổng theo đơn (SKU ghi sổ)",
        "Tổng phiếu xuất kho (SKU ghi sổ)",
        "Tổng phiếu chuyển kho (SKU ghi sổ)",
        "Tổng cộng (SKU ghi sổ)",
    ]
```

Thứ tự dòng nở ra bám thứ tự sort sẵn có của `lines` (`-qty`, rồi `sku`) và thứ tự `component__sku` trong `ComboExpander` — SP-A trước SP-B ở cả hai test.

- [ ] **Step 2: Run test to verify it fails**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py -k dispatch_export`
Expected: FAIL — header thiếu 2 cột mới

- [ ] **Step 3: Sửa nhánh Theo đơn của `DispatchHistoryExport.write`**

Thay khối từ `data = MergedIssueHistory.orders_page(...)` đến hết hàm (`exports_definitions.py:280-348`):

```python
        data = MergedIssueHistory.orders_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
        headers = [
            "Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kho", "Người thực hiện", "SKU",
            "Tên sản phẩm", "Thuộc combo", "Ghi chú", "Số SKU", "SL xuất",
        ]
        writer.write_header(headers)
        # ... giữ nguyên khối notes_by_code + pending hiện có ...
        truncated = _export_truncated(data["total"])
        for row in _truncation_rows(headers, truncated, len(data["items"])):
            _emit(row)

        # Nở SKU combo ra sản phẩm con — 2 query cho cả trang, không N+1.
        expander = ComboExpander.for_items(list(pending) + list(data["items"]))
        expanded_total = 0

        def _emit_lines(prefix: list, lines, suffix_before_qty: list) -> None:
            """Một dòng cho MỖI mã hàng; đơn chưa khớp (không có line) vẫn giữ 1 dòng."""
            nonlocal expanded_total
            rows = expander.expand(lines)
            if not rows:
                _emit(prefix + ["", "", ""] + suffix_before_qty + [""])
                return
            for r in rows:
                qty = _display_qty(r["qty"], r["gross"])
                expanded_total += qty
                _emit(prefix + [r["sku"], r["name"], r["combo_sku"]] + suffix_before_qty + [qty])

        for p in pending:
            _emit_lines(
                [
                    _fmt_dt(p.get("created_at")),
                    _pending_mark(p),
                    p.get("code") or "",
                    p.get("warehouse_name") or "",
                    p.get("performed_by") or "",
                ],
                p.get("lines"),
                [p.get("note") or "", p.get("sku_count") or ""],
            )
        for r in data["items"]:
            _emit_lines(
                [
                    _fmt_dt(r.get("last_at")),
                    _issue_order_code(r),
                    r.get("shipping_code") or "",
                    r.get("warehouse_name") or "",
                    r.get("performed_by") or "",
                ],
                r.get("lines"),
                [
                    notes_by_code.get(r.get("shipping_code") or "", ""),
                    r.get("sku_count") or 0,
                ],
            )
        # Footer ghi sổ (đối chiếu được với tồn kho) GIỮ NGUYÊN con số, chỉ nói rõ
        # trong nhãn là tính theo SKU ghi sổ — combo tính 1. Dòng cuối mới là tổng
        # các dòng CÓ TRONG FILE sau khi nở combo.
        _emit(["Tổng theo đơn (SKU ghi sổ)"] + [""] * 9 + [data["sale_quantity"]])
        _emit(["Tổng phiếu xuất kho (SKU ghi sổ)"] + [""] * 9 + [data["doc_quantity"]])
        _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)"] + [""] * 9 + [data["transfer_quantity"]])
        _emit(["Tổng cộng (SKU ghi sổ)"] + [""] * 9 + [data["total_quantity"]])
        _emit(["Tổng SL sau nổ combo"] + [""] * 9 + [expanded_total])
```

Thêm import ở đầu file:

```python
from apps.inventory.application.services.combo_expansion import ComboExpander
```

Bỏ `_lines_text` khỏi khối import từ `flow_history_excel` (Task 7 sẽ xoá hàm đó).

- [ ] **Step 4: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py -k dispatch`
Expected: PASS toàn bộ (kể cả các test dispatch cũ — nếu test cũ đỏ vì đổi header, cập nhật chúng theo bố cục mới)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/exports_definitions.py backend/apps/inventory/tests/test_inventory_export_definitions.py
git commit -m "feat(inventory): one row per sku in dispatch history export"
```

---

### Task 5: Tab Nhập hoàn — tách dòng theo mã hàng (chế độ Theo đơn)

**Files:**
- Modify: `backend/apps/inventory/exports_definitions.py:410-489` (`ReturnHistoryExport.write`, nhánh `group != "sku"`)
- Test: `backend/apps/inventory/tests/test_inventory_export_definitions.py`

**Interfaces:**
- Consumes: `ComboExpander` (Task 3), khuôn `_emit_lines` từ Task 4 (viết lại tại chỗ — hai tab khác bộ cột nên không gộp hàm chung).
- Produces: header tab Nhập hoàn:
  `["Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn", "Kho", "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Loại hoàn", "Ghi chú", "SL hoàn"]`

- [ ] **Step 1: Write the failing test**

Thêm hai helper dựng dữ liệu (đặt cạnh `_seed_combo_dispatch` của Task 4):

```python
def _seed_return_two_skus():
    """Phiếu hoàn gồm SP-A ×2 và SP-B ×1 (đã xuất trước đó rồi mới hoàn)."""
    wh, order, a, b = _seed_two_skus()
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[
            {"product_id": a.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED},
            {"product_id": b.id, "quantity": 1, "return_type": ReturnType.DELIVERY_RETURNED},
        ],
    )
    return wh, order


def _seed_return_combo():
    """Phiếu hoàn 2 combo; COMBO-X = SP-A ×1 + SP-B ×2."""
    wh, order, combo = _seed_combo("R")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[{"product_id": combo.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    return wh, order, combo
```

`create_and_post_entry(warehouse_id=...)` là service GHI — tham số **số ít**, không đổi thành `warehouse_ids`.

Rồi các test:

```python
def test_return_export_splits_one_row_per_sku():
    _seed_return_two_skus()
    definition = get_definition("return_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    assert writer.headers == [
        "Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn", "Kho",
        "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Loại hoàn",
        "Ghi chú", "SL hoàn",
    ]
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[6], r[11]) for r in detail] == [("SP-A", 2), ("SP-B", 1)]


def test_return_export_expands_combo_rows():
    _seed_return_combo()
    definition = get_definition("return_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[6], r[8], r[11]) for r in detail] == [
        ("SP-A", "COMBO-X", 2),
        ("SP-B", "COMBO-X", 4),
    ]
    assert writer.rows[-1][0] == "Tổng SL sau nổ combo"
    assert writer.rows[-1][11] == sum(r[11] for r in detail)
```

- [ ] **Step 2: Run test to verify it fails**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py -k return_export`
Expected: FAIL — header thiếu 2 cột mới

- [ ] **Step 3: Sửa nhánh Theo đơn của `ReturnHistoryExport.write`**

Thay khối từ `headers = [...]` đến hết hàm:

```python
        headers = [
            "Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn",
            "Kho", "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo",
            "Loại hoàn", "Ghi chú", "SL hoàn",
        ]
        writer.write_header(headers)
        # ... giữ nguyên khối gộp scan_notes + pending hiện có ...
        truncated = _export_truncated(data["total"])
        for row in _truncation_rows(headers, truncated, len(data["items"])):
            _emit(row)

        expander = ComboExpander.for_items(list(pending) + list(data["items"]))
        expanded_total = 0

        def _emit_lines(prefix: list, lines, tail: list) -> None:
            nonlocal expanded_total
            rows = expander.expand(lines)
            if not rows:
                _emit(prefix + ["", "", ""] + tail + [""])
                return
            for r in rows:
                expanded_total += r["qty"]
                _emit(prefix + [r["sku"], r["name"], r["combo_sku"]] + tail + [r["qty"]])

        for p in pending:
            _emit_lines(
                [
                    _fmt_dt(p.get("created_at")),
                    _pending_mark(p),
                    "",
                    p.get("code") or "",
                    p.get("warehouse_name") or "",
                    p.get("performed_by") or "",
                ],
                p.get("lines"),
                ["", p.get("note") or ""],
            )
        for r in data["items"]:
            _emit_lines(
                [
                    _fmt_dt(r.get("created_at")),
                    r.get("code") or "",
                    r.get("source_order_invoice_code") or r.get("source_order_code") or "",
                    r.get("shipping_code") or "",
                    r.get("warehouse_name") or "",
                    r.get("performed_by") or "",
                ],
                r.get("lines"),
                [r.get("return_type_label") or "", r.get("note") or ""],
            )
        _emit(["Tổng hoàn theo đơn (SKU ghi sổ)"] + [""] * 10 + [data["return_quantity"]])
        _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)"] + [""] * 10 + [data["transfer_quantity"]])
        _emit(["Tổng cộng (SKU ghi sổ)"] + [""] * 10 + [data["total_quantity"]])
        _emit(["Tổng SL sau nổ combo"] + [""] * 10 + [expanded_total])
```

- [ ] **Step 4: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py`
Expected: PASS toàn bộ file

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/exports_definitions.py backend/apps/inventory/tests/test_inventory_export_definitions.py
git commit -m "feat(inventory): one row per sku in return history export"
```

---

### Task 6: Chế độ Theo SKU — nổ và cộng dồn

**Files:**
- Modify: `backend/apps/inventory/exports_definitions.py` (`DispatchHistoryExport.write` nhánh `group == "sku"`, `ReturnHistoryExport.write` nhánh `group == "sku"`)
- Test: `backend/apps/inventory/tests/test_inventory_export_definitions.py`

**Interfaces:**
- Consumes: `ComboExpander.for_product_ids` (Task 3).
- Produces:
  - header tab Xuất theo SKU: `["SKU", "Tên", "Số đơn", "SL theo đơn", "SL phiếu kho", "SL chuyển kho", "SL xuất", "Trong đó từ combo"]`
  - header tab Nhập hoàn theo SKU: `["SKU", "Tên", "Số phiếu", "SL hoàn", "SL chuyển kho", "SL nhập", "Trong đó từ combo"]`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_sku_export_merges_combo_into_components():
    """Dòng COMBO-X biến mất, SL cộng vào SP-A/SP-B; cột cuối tách phần từ combo."""
    _seed_combo_dispatch()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters(group="sku")))
    assert writer.headers[-1] == "Trong đó từ combo"
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [r[0] for r in detail] == ["SP-A", "SP-B"]
    assert [(r[6], r[7]) for r in detail] == [(2, 2), (4, 4)]


def test_return_sku_export_merges_combo_into_components():
    _seed_return_combo()
    definition = get_definition("return_history")
    writer = _write(definition, definition.validate(None, _raw_filters(group="sku")))
    assert writer.headers[-1] == "Trong đó từ combo"
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [r[0] for r in detail] == ["SP-A", "SP-B"]
    assert [(r[5], r[6]) for r in detail] == [(2, 2), (4, 4)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py -k sku_export`
Expected: FAIL — header chưa có cột "Trong đó từ combo"

- [ ] **Step 3: Viết hàm gộp trong `combo_expansion.py`**

Thêm vào `ComboExpander`:

```python
    def merge_sku_rows(self, rows: list[dict], qty_keys: tuple[str, ...]) -> list[dict]:
        """Gộp dòng-theo-SKU sau khi nở combo (dùng cho chế độ Theo SKU).

        Mỗi dòng combo tan ra thành các dòng sản phẩm con với mọi cột SL trong
        ``qty_keys`` được nhân theo công thức, rồi cộng dồn vào dòng của chính SP
        con nếu SKU đó cũng bán lẻ trong kỳ. ``from_combo`` giữ phần đến từ combo.

        ``count`` (Số đơn / Số phiếu) cộng dồn THÔ: một đơn vừa mua lẻ vừa mua
        combo chứa SKU đó sẽ bị đếm hai lần. Trường hợp hiếm và cột này chỉ để
        tham khảo — SL mới là con số nghiệp vụ dùng.
        """
        merged: dict[str, dict] = {}

        def _slot(sku: str, name: str) -> dict:
            row = merged.get(sku)
            if row is None:
                row = {"sku": sku, "name": name, "count": 0, "from_combo": 0}
                row.update({k: 0 for k in qty_keys})
                merged[sku] = row
            return row

        for r in rows:
            product_id = r.get("product_id")
            comps = self._components.get(product_id)
            if not comps:
                row = _slot(r.get("sku") or "", r.get("name") or "")
                row["count"] += r.get("count") or 0
                for k in qty_keys:
                    row[k] += r.get(k) or 0
                continue
            for c in comps:
                row = _slot(c["sku"], c["name"])
                row["count"] += r.get("count") or 0
                for k in qty_keys:
                    value = (r.get(k) or 0) * c["quantity"]
                    row[k] += value
                row["from_combo"] += (r.get(qty_keys[-1]) or 0) * c["quantity"]
        return sorted(merged.values(), key=lambda x: x["sku"])
```

Test bổ sung cho hàm này trong `test_combo_expansion.py`:

```python
def test_merge_sku_rows_multiplies_every_qty_column():
    combo = _product("COMBO-Z", "Combo Z")
    a = _product("SP-E", "Sản phẩm E")
    ProductComponent.objects.create(parent=combo, component=a, quantity=3)
    expander = ComboExpander.for_product_ids([combo.id])
    rows = [{"product_id": combo.id, "sku": "COMBO-Z", "name": "Combo Z", "count": 1, "net": 2, "total": 2}]
    assert expander.merge_sku_rows(rows, ("net", "total")) == [
        {"sku": "SP-E", "name": "Sản phẩm E", "count": 1, "from_combo": 6, "net": 6, "total": 6}
    ]
```

- [ ] **Step 4: Sửa nhánh `group == "sku"` của `DispatchHistoryExport.write`**

```python
        if params["group"] == "sku":
            data = MergedIssueHistory.skus_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
            headers = [
                "SKU", "Tên", "Số đơn", "SL theo đơn", "SL phiếu kho",
                "SL chuyển kho", "SL xuất", "Trong đó từ combo",
            ]
            writer.write_header(headers)
            truncated = _export_truncated(data["total"])
            for row in _truncation_rows(headers, truncated, len(data["items"])):
                _emit(row)
            expander = ComboExpander.for_product_ids([r.get("product_id") for r in data["items"]])
            # `sale` là SL theo đơn đã chuẩn hoá qua `_display_qty` TRƯỚC khi nở —
            # nở rồi mới chuẩn hoá sẽ nhân cả phần đảo sổ.
            source = [
                {
                    "product_id": r.get("product_id"),
                    "sku": r.get("sku") or "",
                    "name": r.get("name") or "",
                    "count": r.get("order_count") or 0,
                    "sale": _display_qty(r.get("net"), r.get("dispatched")),
                    "doc": r.get("doc_quantity") or 0,
                    "transfer": r.get("transfer_quantity") or 0,
                    "total": _display_qty(
                        (r.get("net") or 0) + (r.get("doc_quantity") or 0) + (r.get("transfer_quantity") or 0),
                        (r.get("dispatched") or 0) + (r.get("doc_quantity") or 0) + (r.get("transfer_quantity") or 0),
                    ),
                }
                for r in data["items"]
            ]
            for row in expander.merge_sku_rows(source, ("sale", "doc", "transfer", "total")):
                _emit(
                    [
                        row["sku"], row["name"], row["count"], row["sale"],
                        row["doc"], row["transfer"], row["total"], row["from_combo"],
                    ]
                )
            _emit(["Tổng theo đơn (SKU ghi sổ)", "", "", data["sale_quantity"], "", "", "", ""])
            _emit(["Tổng phiếu xuất kho (SKU ghi sổ)", "", "", "", data["doc_quantity"], "", "", ""])
            _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)", "", "", "", "", data["transfer_quantity"], "", ""])
            _emit(["Tổng cộng (SKU ghi sổ)", "", "", "", "", "", data["total_quantity"], ""])
            return
```

- [ ] **Step 5: Sửa nhánh `group == "sku"` của `ReturnHistoryExport.write`**

```python
        if params["group"] == "sku":
            data = MergedReturnHistory.skus_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
            headers = [
                "SKU", "Tên", "Số phiếu", "SL hoàn", "SL chuyển kho", "SL nhập",
                "Trong đó từ combo",
            ]
            writer.write_header(headers)
            truncated = _export_truncated(data["total"])
            for row in _truncation_rows(headers, truncated, len(data["items"])):
                _emit(row)
            expander = ComboExpander.for_product_ids([r.get("product_id") for r in data["items"]])
            source = [
                {
                    "product_id": r.get("product_id"),
                    "sku": r.get("sku") or "",
                    "name": r.get("name") or "",
                    "count": r.get("return_count") or 0,
                    "qty": r.get("qty_total") or 0,
                    "transfer": r.get("transfer_quantity") or 0,
                    "total": (r.get("qty_total") or 0) + (r.get("transfer_quantity") or 0),
                }
                for r in data["items"]
            ]
            for row in expander.merge_sku_rows(source, ("qty", "transfer", "total")):
                _emit(
                    [
                        row["sku"], row["name"], row["count"], row["qty"],
                        row["transfer"], row["total"], row["from_combo"],
                    ]
                )
            _emit(["Tổng hoàn theo đơn (SKU ghi sổ)", "", "", data["return_quantity"], "", "", ""])
            _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)", "", "", "", data["transfer_quantity"], "", ""])
            _emit(["Tổng cộng (SKU ghi sổ)", "", "", "", "", data["total_quantity"], ""])
            return
```

- [ ] **Step 6: Run test to verify it passes**

Run: pytest với `<paths>` = `apps/inventory/tests/test_inventory_export_definitions.py apps/inventory/tests/test_combo_expansion.py`
Expected: PASS toàn bộ

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/exports_definitions.py backend/apps/inventory/application/services/combo_expansion.py backend/apps/inventory/tests/
git commit -m "feat(inventory): expand combos in sku-grouped history exports"
```

---

### Task 7: Dọn code chết ở `flow_history_excel.py`

**Files:**
- Modify: `backend/apps/inventory/infrastructure/flow_history_excel.py` (xoá `export_issue_orders`, `export_return_notes`, `export_issue_skus`, `export_return_skus`, `_lines_text`, `_workbook_response`, import `openpyxl`/`HttpResponse`/`io`/`append_clean_row` nếu không còn dùng)
- Modify: `backend/apps/inventory/tests/test_flow_history_excel.py` (xoá test của các hàm trên)

**Interfaces:**
- Produces: module chỉ còn các helper `_fmt_dt`, `_pending_mark`, `_truncation_rows`, `_display_qty`, `_issue_order_code` — đúng những gì `exports_definitions.py` import.

- [ ] **Step 1: Xác nhận thật sự là code chết**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-combo-line-split && grep -rn "export_issue_orders\|export_return_notes\|export_issue_skus\|export_return_skus\|_lines_text" --include=*.py backend/apps | grep -v "tests/"
```

Expected: không có dòng nào ngoài chính `flow_history_excel.py`. Nếu có, DỪNG và báo lại — giả định của plan sai.

- [ ] **Step 2: Xoá 4 hàm export + `_lines_text` + `_workbook_response`**

Xoá luôn các import chỉ phục vụ chúng (`io`, `openpyxl`, `HttpResponse`, `append_clean_row`, hằng `_XLSX`). Giữ nguyên phần còn lại của file.

- [ ] **Step 3: Xoá test tương ứng**

Trong `backend/apps/inventory/tests/test_flow_history_excel.py`, xoá mọi test gọi 4 hàm đã xoá. Nếu file rỗng sau khi xoá, xoá luôn file. Cảnh báo về ruff: sau khi xoá test, kiểm tra file còn `import *` nào không trước khi tin ruff báo import thừa.

- [ ] **Step 4: Run test + lint**

Run: pytest với `<paths>` = `apps/inventory/tests/`
Expected: PASS, không có ImportError

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/infrastructure/flow_history_excel.py backend/apps/inventory/tests/test_flow_history_excel.py
git commit -m "refactor(inventory): drop dead flow history excel exporters"
```

---

### Task 8: Frontend — dialog khai thành phần combo

**Files:**
- Create: `frontend/src/lib/api/product-components.ts`
- Create: `frontend/src/features/products/components/ComboComponentsDialog.tsx`
- Create: `frontend/src/features/products/__tests__/combo-components-dialog.test.tsx`
- Modify: `frontend/src/pages/ProductsPage.tsx` (nút ở hàng desktop ~dòng 572-603 và thẻ mobile ~dòng 655-681)
- Modify: `frontend/src/api/schema.d.ts` (áp hunk sinh từ `schema.yaml` của Task 2)

**Interfaces:**
- Consumes: `GET|PUT /api/orders/products/{id}/components` (Task 2).
- Produces:
  - `getProductComponents(productId: number): Promise<ProductComponentRow[]>`
  - `putProductComponents(productId: number, rows: ProductComponentInput[]): Promise<ProductComponentRow[]>`
  - `<ComboComponentsDialog product={product} open={boolean} onOpenChange={(v: boolean) => void} />`

- [ ] **Step 1: Sinh lại type từ schema đã sửa**

```bash
cd frontend && npx openapi-typescript ../backend/schema.yaml -o /tmp/x.d.ts && npx prettier --write /tmp/x.d.ts
```

Chỉ áp hunk của endpoint mới vào `src/api/schema.d.ts`, kể cả khối JSDoc `@description` phía trên type (quên khối này là để lại drift cho lần regen sau).

- [ ] **Step 2: Viết api client**

Tạo `frontend/src/lib/api/product-components.ts`:

```typescript
import { fetchJson } from "@/services/http";
import type { components } from "@/api/typed";

const API_BASE = "/api";

export type ProductComponentRow = components["schemas"]["ProductComponentRow"];

export interface ProductComponentInput {
  component_id: number;
  quantity: number;
}

export async function getProductComponents(productId: number): Promise<ProductComponentRow[]> {
  return fetchJson(`${API_BASE}/orders/products/${productId}/components`);
}

export async function putProductComponents(
  productId: number,
  rows: ProductComponentInput[],
): Promise<ProductComponentRow[]> {
  return fetchJson(`${API_BASE}/orders/products/${productId}/components`, {
    method: "PUT",
    body: JSON.stringify({ components: rows }),
  });
}
```

- [ ] **Step 3: Write the failing test**

Tạo `frontend/src/features/products/__tests__/combo-components-dialog.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ComboComponentsDialog } from "../components/ComboComponentsDialog";

const getProductComponents = vi.fn();
const putProductComponents = vi.fn();

vi.mock("@/lib/api/product-components", () => ({
  getProductComponents: (...a: unknown[]) => getProductComponents(...a),
  putProductComponents: (...a: unknown[]) => putProductComponents(...a),
}));

vi.mock("@/lib/api/products", () => ({
  getProducts: async () => [
    { id: 2, sku: "SP-A", name: "Sản phẩm A" },
    { id: 3, sku: "SP-B", name: "Sản phẩm B" },
  ],
}));

const PRODUCT = { id: 1, sku: "COMBO-X", name: "Combo X" };

beforeEach(() => {
  getProductComponents.mockResolvedValue([]);
  putProductComponents.mockResolvedValue([]);
});

describe("ComboComponentsDialog", () => {
  it("lưu danh sách thành phần đã nhập", async () => {
    const user = userEvent.setup();
    render(<ComboComponentsDialog product={PRODUCT} open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("button", { name: /thêm thành phần/i }));
    await user.click(screen.getByRole("combobox", { name: /sản phẩm thành phần/i }));
    await user.click(await screen.findByRole("option", { name: /SP-A/ }));
    await user.clear(screen.getByRole("spinbutton", { name: /số lượng/i }));
    await user.type(screen.getByRole("spinbutton", { name: /số lượng/i }), "2");
    await user.click(screen.getByRole("button", { name: /^lưu$/i }));

    await waitFor(() =>
      expect(putProductComponents).toHaveBeenCalledWith(1, [{ component_id: 2, quantity: 2 }]),
    );
  });

  it("chặn lưu khi số lượng nhỏ hơn 1", async () => {
    const user = userEvent.setup();
    getProductComponents.mockResolvedValue([
      { component_id: 2, sku: "SP-A", name: "Sản phẩm A", quantity: 1 },
    ]);
    render(<ComboComponentsDialog product={PRODUCT} open onOpenChange={() => {}} />);

    const qty = await screen.findByRole("spinbutton", { name: /số lượng/i });
    await user.clear(qty);
    await user.type(qty, "0");
    await user.click(screen.getByRole("button", { name: /^lưu$/i }));

    expect(await screen.findByText(/số lượng phải từ 1/i)).toBeInTheDocument();
    expect(putProductComponents).not.toHaveBeenCalled();
  });

  it("chặn chọn trùng sản phẩm", async () => {
    const user = userEvent.setup();
    getProductComponents.mockResolvedValue([
      { component_id: 2, sku: "SP-A", name: "Sản phẩm A", quantity: 1 },
    ]);
    render(<ComboComponentsDialog product={PRODUCT} open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("button", { name: /thêm thành phần/i }));
    const selects = screen.getAllByRole("combobox", { name: /sản phẩm thành phần/i });
    await user.click(selects[1]);
    await user.click(await screen.findByRole("option", { name: /SP-A/ }));
    await user.click(screen.getByRole("button", { name: /^lưu$/i }));

    expect(await screen.findByText(/đã có trong danh sách/i)).toBeInTheDocument();
    expect(putProductComponents).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/features/products/__tests__/combo-components-dialog.test.tsx`
Expected: FAIL — không import được `ComboComponentsDialog`

- [ ] **Step 5: Viết dialog**

Tạo `frontend/src/features/products/components/ComboComponentsDialog.tsx` theo khuôn dialog sẵn có của repo (`Dialog` + `FormDialogShell` từ `@/components/ui/form-dialog-content`, `Select` của shadcn, icon `lucide-react`):

- state: `rows: {component_id: number | null; quantity: number}[]`, `error: string`
- nạp bằng `getProductComponents(product.id)` khi `open` chuyển sang true
- danh sách chọn: `getProducts({ limit: 10000 })`, loại chính `product.id` khỏi danh sách
- mỗi dòng: `Select` (`aria-label="Sản phẩm thành phần"`), `Input type="number"` (`aria-label="Số lượng"`), nút xoá dòng (`aria-label="Xóa thành phần"`)
- nút "Thêm thành phần" đẩy một dòng rỗng
- validate trước khi gọi API: số lượng ≥ 1 (`"Số lượng phải từ 1 trở lên."`), không trùng sản phẩm (`"Sản phẩm này đã có trong danh sách."`), bỏ qua dòng chưa chọn sản phẩm
- lưu: `putProductComponents(product.id, rows)`, thành công thì `onOpenChange(false)`
- lỗi từ API: hiện `detail` trả về

Ghi chú React Compiler: **không đọc `ref.current` trong thân render** (repo đã dính bẫy này một lần — nút Xuất Excel kẹt trên prod, vitest không bắt được vì thiếu compiler). Mọi state điều khiển form phải là `useState`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/features/products/__tests__/combo-components-dialog.test.tsx`
Expected: 3 passed

- [ ] **Step 7: Gắn nút vào trang Sản phẩm**

Trong `frontend/src/pages/ProductsPage.tsx`:
- thêm state `const [comboProduct, setComboProduct] = useState<Product | null>(null);`
- thêm nút vào hàng desktop (sau nút "Nội dung trang", trước nút Sửa) và vào thẻ mobile:

```tsx
        <Button
          title={t("products.actions.combo", "Thành phần combo")}
          aria-label={t("products.actions.combo", "Thành phần combo")}
          size="icon-sm"
          variant="outline"
          className="border-border bg-background text-primary hover:bg-accent hover:text-accent-foreground ml-1"
          onClick={() => setComboProduct(product)}
        >
          <Boxes className="w-4 h-4" />
        </Button>
```

- import `Boxes` từ `lucide-react`
- render dialog một lần ở cuối trang:

```tsx
      {comboProduct ? (
        <ComboComponentsDialog
          product={comboProduct}
          open
          onOpenChange={(v) => {
            if (!v) setComboProduct(null);
          }}
        />
      ) : null}
```

- [ ] **Step 8: Chạy toàn bộ test frontend liên quan**

Run: `cd frontend && npm run test -- src/features/products src/pages/__tests__ 2>&1 | tail -20`
Expected: PASS (nếu `RequireAuth.test.tsx` đỏ thì đó là nợ có sẵn trên master, không phải do task này)

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/api/product-components.ts \
        frontend/src/features/products/components/ComboComponentsDialog.tsx \
        frontend/src/features/products/__tests__/combo-components-dialog.test.tsx \
        frontend/src/pages/ProductsPage.tsx frontend/src/api/schema.d.ts
git commit -m "feat(products): dialog to declare combo components"
```

---

### Task 9: Chạy trọn suite liên quan + tài liệu

**Files:**
- Create: `docs/flow-history-combo-expansion-2026-08-12.md`

- [ ] **Step 1: Chạy suite backend của 2 app bị đụng**

Suite dài — chạy nền bằng `docker run -d` rồi chờ, không để shell nền của harness cắt ngang:

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-combo-line-split && MSYS_NO_PATHCONV=1 docker run -d --name combo-suite --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_combo_suite \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=10 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory apps/orders -q --create-db'
```

Chờ xong rồi `docker logs combo-suite | tail -30`, sau đó `docker rm combo-suite`.
Expected: không có test đỏ MỚI. Đối chiếu nợ đỏ có sẵn trên master trước khi kết luận.

- [ ] **Step 2: Viết ghi chú phát hành**

Tạo `docs/flow-history-combo-expansion-2026-08-12.md` nêu:
- cách khai thành phần combo trên màn Sản phẩm
- hai cột mới ở file Theo đơn, một cột mới ở file Theo SKU
- ý nghĩa "Tổng ... (SKU ghi sổ)" so với "Tổng SL sau nổ combo"
- BOM không có phiên bản: sửa thành phần làm đổi số liệu kỳ cũ
- màn hình web vẫn gộp, chỉ file Excel nở combo

- [ ] **Step 3: Commit + push + tạo MR**

```bash
git add docs/flow-history-combo-expansion-2026-08-12.md
git commit -m "docs: release note for flow history combo expansion"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="Flow history: one row per SKU and combo expansion in Excel exports" \
  origin feat/flow-history-combo-line-split
```

Lấy link MR trong output của lệnh push và gửi cho người dùng. Sau khi đã gửi link, **không đẩy thêm commit vào nhánh này** — thay đổi mới đi MR mới.

---

## Ghi chú cho người thực thi

- Không đụng bất cứ file nào trong luồng ghi `StockMovement`. Nếu thấy mình đang sửa `stock_command_service`, `return_entry_service` hay tương tự → sai hướng, dừng lại.
- Trang Lịch sử nhập xuất có hai nhánh code lọc (màn hình và job export). Plan này **chỉ** đụng nhánh export; không sửa `flow_history_views.py`.
- Khi sửa test cũ vì đổi header: chỉ đổi phần chỉ số cột, không nới lỏng assert thành `in` hay `any` để cho qua.
