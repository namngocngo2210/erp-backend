# Loại hoàn theo từng dòng phiếu trả — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép chọn loại hoàn (Hàng hoàn / Trả ngoài / Bảo hành / Đổi lỗi) trên TỪNG dòng hàng của phiếu trả, mặc định Hàng hoàn, ghi sổ nhập kho theo đúng loại từng dòng; hiển thị người tạo phiếu; dialog chi tiết responsive mobile.

**Architecture:** Thêm `return_type` (blank = kế thừa loại phiếu) vào `ReturnNoteLine`; `post_return` tính state movement theo loại hiệu lực từng dòng và đánh dấu đơn RETURNED khi có ≥1 dòng Hàng hoàn. FE: dialog chi tiết thêm select per dòng (PATCH gửi lại toàn bộ `lines` — hành vi replace sẵn có của `update_draft`), các luồng tạo phiếu gửi mặc định `delivery_returned`.

**Tech Stack:** Django/DRF + drf-spectacular, React + TypeScript + React Query + shadcn/ui, vitest, pytest (docker image `desi-order-web-test`).

**Spec:** `docs/superpowers/specs/2026-07-06-return-line-type-design.md`

## Global Constraints

- Branch: `feat/return-line-type`, worktree `c:\Users\MYPC\Desktop\workspace\desi-order-camscan` (đã checkout sẵn). MỌI lệnh chạy từ worktree này, KHÔNG phải checkout chính.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng Co-Authored-By.
- BE test chạy qua docker (xem lệnh trong từng task; trước khi chạy lần đầu, `docker ps` xác nhận stack đang chạy là `desi-order-postgres-1`/`desi-order-redis-1` → network `desi-order_default`, redis password `testpassword`; nếu là stack khác xem memory `running-backend-tests`).
- KHÔNG bao giờ dùng `cache.clear()` trong test (FLUSHDB vào Redis thật).
- FE test: `npx vitest run <path>` từ `frontend/` của worktree (node_modules đã cài).
- Sau khi thêm migration, pytest cần `--create-db` cho lần chạy đầu.
- Giá trị mặc định loại hoàn ở mọi nơi: chuỗi `"delivery_returned"`.

---

### Task 1: Model — `ReturnNoteLine.return_type` + migration

**Files:**
- Modify: `backend/apps/inventory/models.py` (class `ReturnNoteLine`, ~dòng 344)

**Interfaces:**
- Produces: field `ReturnNoteLine.return_type: str` (choices `ReturnType`, blank default `""`), migration `0013_returnnoteline_return_type`.

- [ ] **Step 1: Thêm field vào model**

Trong `backend/apps/inventory/models.py`, class `ReturnNoteLine`, thêm sau field `quantity`:

```python
    # Loại hoàn riêng của dòng; trống = kế thừa return_type của phiếu.
    return_type = models.CharField(
        max_length=20, choices=ReturnType.choices, blank=True, default=""
    )
```

- [ ] **Step 2: Sinh migration**

Từ repo root của worktree (Git Bash):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations inventory
```

Expected: tạo `backend/apps/inventory/migrations/0013_returnnoteline_return_type.py` (tên có thể khác đuôi — chấp nhận, miễn là AddField return_type).

- [ ] **Step 3: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/
git commit -m "feat(inventory): add per-line return_type to ReturnNoteLine"
```

---

### Task 2: Backend — ghi sổ theo loại từng dòng (TDD)

**Files:**
- Create: `backend/apps/inventory/tests/test_return_line_type.py`
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (`post_return` dòng 130-172, `create_return` dòng 119-127, `update_draft` dòng 208-218)

**Interfaces:**
- Consumes: `ReturnNoteLine.return_type` (Task 1).
- Produces: `create_return`/`update_draft` nhận key `return_type` trong từng dict của `lines`; `post_return` ghi movement theo loại hiệu lực (`line.return_type or rn.return_type`), lỗi `ValueError` chứa SKU khi dòng không xác định được loại; đánh dấu đơn RETURNED khi có ≥1 dòng hiệu lực `delivery_returned`.

- [ ] **Step 1: Viết test fail**

Tạo `backend/apps/inventory/tests/test_return_line_type.py`:

```python
"""Loại hoàn theo từng dòng phiếu trả: ghi sổ per-line state, validation,
đánh dấu đơn RETURNED khi có dòng Hàng hoàn."""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import StockItem, StockMovement
from apps.orders.models import Order, OrderStatus, Product
from apps.warehouses.models import Warehouse


class ReturnLineTypeTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LT", name="Kho LineType")
        self.p1 = Product.objects.create(name="SP1", sku="SKU-LT1")
        self.p2 = Product.objects.create(name="SP2", sku="SKU-LT2")
        self.p3 = Product.objects.create(name="SP3", sku="SKU-LT3")
        self.channel = Channel.objects.create(code="ch-lt", name="LT Channel")
        self.customer = Customer.objects.create(
            full_name="Khach LT",
            phone_raw="0912345670",
            phone_normalized="0912345670",
            code="CUST-LT",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach LT",
            phone="0912345670",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _create(self, note_type, lines):
        return ReturnNoteService.create_return(
            return_type=note_type,
            warehouse_id=self.warehouse.id,
            lines=lines,
            source_order_id=self.order.id,
        )

    def _qty(self, product, state):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state=state
        ).first()
        return item.quantity if item else 0

    def test_line_return_type_persisted_via_create_and_update(self):
        rn = self._create(
            "delivery_returned",
            [{"product_id": self.p1.id, "quantity": 1, "return_type": "warranty"}],
        )
        self.assertEqual(rn.lines.get().return_type, "warranty")

        ReturnNoteService.update_draft(
            return_note_id=rn.id,
            data={
                "lines": [
                    {"product_id": self.p1.id, "quantity": 2, "return_type": "defect"}
                ]
            },
        )
        line = rn.lines.get()
        self.assertEqual(line.return_type, "defect")
        self.assertEqual(line.quantity, 2)

    def test_post_mixed_line_types_records_per_line_state(self):
        rn = self._create(
            "delivery_returned",
            [
                {"product_id": self.p1.id, "quantity": 1},  # trống → kế thừa Hàng hoàn
                {"product_id": self.p2.id, "quantity": 1, "return_type": "warranty"},
                {"product_id": self.p3.id, "quantity": 1, "return_type": "defect"},
            ],
        )
        ReturnNoteService.post_return(return_note_id=rn.id)
        states = dict(
            StockMovement.objects.filter(
                reference_type="return", reference_id=rn.id
            ).values_list("product_id", "state")
        )
        self.assertEqual(
            states,
            {self.p1.id: "sellable", self.p2.id: "warranty", self.p3.id: "defect"},
        )

    def test_post_blocked_when_line_type_unresolved(self):
        rn = self._create("", [{"product_id": self.p1.id, "quantity": 1}])
        with self.assertRaisesMessage(ValueError, "SKU-LT1"):
            ReturnNoteService.post_return(return_note_id=rn.id)
        self.assertEqual(rn.lines.count(), 1)  # phiếu còn nguyên nháp

    def test_any_delivery_returned_line_marks_order_returned(self):
        rn = self._create(
            "warranty",
            [
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                },
                {"product_id": self.p2.id, "quantity": 1},  # kế thừa warranty
            ],
        )
        ReturnNoteService.post_return(return_note_id=rn.id)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_no_delivery_returned_line_keeps_order_status(self):
        before = int(self.order.status)
        rn = self._create(
            "warranty", [{"product_id": self.p1.id, "quantity": 1}]
        )
        ReturnNoteService.post_return(return_note_id=rn.id)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), before)

    def test_cancel_mixed_reverses_each_state(self):
        rn = self._create(
            "delivery_returned",
            [
                {"product_id": self.p1.id, "quantity": 1},
                {"product_id": self.p2.id, "quantity": 1, "return_type": "warranty"},
            ],
        )
        ReturnNoteService.post_return(return_note_id=rn.id)
        self.assertEqual(self._qty(self.p1, "sellable"), 1)
        self.assertEqual(self._qty(self.p2, "warranty"), 1)

        ReturnNoteService.cancel_return(return_note_id=rn.id)
        self.assertEqual(self._qty(self.p1, "sellable"), 0)
        self.assertEqual(self._qty(self.p2, "warranty"), 0)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/test_return_line_type.py -q --create-db
```

Expected: FAIL — `test_line_return_type_persisted...` (return_type == "" vì create chưa lưu), `test_post_mixed...` (state cả 3 dòng = sellable), `test_post_blocked...` (message cũ "Phiếu chưa phân loại" không chứa SKU), `test_any_delivery_returned...` (status không đổi vì phiếu là warranty).

- [ ] **Step 3: Sửa service**

Trong `backend/apps/inventory/application/services/return_note_service.py`:

(a) `create_return` — bulk_create thêm return_type:

```python
        ReturnNoteLine.objects.bulk_create([
            ReturnNoteLine(
                return_note=rn,
                product_id=line_data["product_id"],
                quantity=line_data["quantity"],
                condition_note=line_data.get("condition_note", ""),
                return_type=line_data.get("return_type", ""),
            )
            for line_data in lines
        ])
```

(b) `update_draft` — nhánh `if "lines" in data:` tương tự:

```python
            ReturnNoteLine.objects.bulk_create([
                ReturnNoteLine(
                    return_note=rn,
                    product_id=line["product_id"],
                    quantity=line["quantity"],
                    condition_note=line.get("condition_note", ""),
                    return_type=line.get("return_type", ""),
                )
                for line in data["lines"]
            ])
```

(c) `post_return` — thay đoạn từ `if not rn.return_type:` đến hết vòng `for line in rn.lines.all():` bằng:

```python
        lines = list(rn.lines.select_related("product"))
        resolved: list[tuple[ReturnNoteLine, str]] = []
        for line in lines:
            effective = line.return_type or rn.return_type
            if not effective:
                raise ValueError(
                    f"Dòng {line.product.sku} chưa xác định loại trả hàng — "
                    "chọn loại cho dòng hoặc loại chung của phiếu trước khi ghi sổ."
                )
            resolved.append((line, effective))
        ReturnNoteService._guard_double_credit(rn)
        for line, effective in resolved:
            StockCommandService.record_movement(
                warehouse_id=rn.warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.RETURN_IN,
                state=_RETURN_STATE[effective],
                reference_type=_REFERENCE_TYPE,
                reference_id=rn.id,
                user_id=user_id,
            )
```

và đổi điều kiện đánh dấu đơn RETURNED (giữ nguyên thân khối):

```python
        has_delivery_returned = any(
            effective == ReturnType.DELIVERY_RETURNED for _, effective in resolved
        )
        if has_delivery_returned and rn.source_order_id:
```

Lưu ý: biến `state = _RETURN_STATE[rn.return_type]` cũ xoá đi; dòng `if not rn.return_type: raise ...` cũ xoá (validation mới per-line thay thế).

- [ ] **Step 4: Chạy lại test file mới + toàn bộ test inventory & orders liên quan**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/ apps/orders/tests/test_dispatch_stock_cycle.py -q
```

Expected: PASS toàn bộ (test cũ `test_stock_sales_cycle.py` dùng loại cấp phiếu phải vẫn xanh — dòng trống kế thừa).

Nếu có test cũ assert message "Phiếu chưa phân loại" → cập nhật test đó theo message mới (grep `"Phiếu chưa phân loại"` trong `backend/apps` để tìm).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_line_type.py
git commit -m "feat(inventory): post return notes with per-line return type"
```

---

### Task 3: Backend — API serializers + `created_by_name` (TDD)

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py`
- Modify: `backend/apps/inventory/transport/views/return_views.py` (4 chỗ queryset `select_related("warehouse", "source_order")` → thêm `"created_by"`: list GET dòng ~64-68, POST refetch ~111-115, PATCH refetch ~155-159, GET detail ~163-167, post_return_view refetch ~194-198, cancel_return_view refetch ~225-229)
- Create: `backend/apps/inventory/tests/test_return_api_line_type.py`

**Interfaces:**
- Consumes: service nhận `return_type` per line (Task 2).
- Produces: response phiếu có `created_by_name: str` và mỗi line có `return_type: str`; request create/update nhận `lines[].return_type` optional.

- [ ] **Step 1: Viết test fail**

Tạo `backend/apps/inventory/tests/test_return_api_line_type.py`:

```python
"""API phiếu trả: nhận/trả return_type per line + created_by_name."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User
from apps.warehouses.models import Warehouse
from apps.orders.models import Product


class ReturnApiLineTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="kho-lt", password="x", is_superuser=True
        )
        self.user.display_name = "Kho Một"
        self.user.save(update_fields=["display_name"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.warehouse = Warehouse.objects.create(code="WH-API-LT", name="Kho API LT")
        self.product = Product.objects.create(name="SP API", sku="SKU-API-LT")

    def _create_payload(self, **over):
        payload = {
            "warehouse_id": self.warehouse.id,
            "lines": [
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "warranty",
                }
            ],
        }
        payload.update(over)
        return payload

    def test_create_returns_line_type_and_creator(self):
        res = self.client.post(
            "/api/inventory/returns", self._create_payload(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["created_by_name"], "Kho Một")
        self.assertEqual(res.data["lines"][0]["return_type"], "warranty")

    def test_patch_updates_line_type(self):
        res = self.client.post(
            "/api/inventory/returns", self._create_payload(), format="json"
        )
        rid = res.data["id"]
        res2 = self.client.patch(
            f"/api/inventory/returns/{rid}",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "defect",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(res2.status_code, 200, res2.content)
        self.assertEqual(res2.data["lines"][0]["return_type"], "defect")

    def test_invalid_line_type_rejected(self):
        res = self.client.post(
            "/api/inventory/returns",
            self._create_payload(
                lines=[
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "bogus",
                    }
                ]
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
```

Lưu ý: nếu URL thật có đuôi khác (xem `backend/apps/inventory/urls.py`, endpoint `returns`), chỉnh path trong test cho đúng TRƯỚC khi chạy. Nếu superuser không bypass được `require_section("returns")`, xem cách các test API khác trong `apps/inventory/tests/` cấp quyền và làm theo.

- [ ] **Step 2: Chạy, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/test_return_api_line_type.py -q
```

Expected: FAIL — `created_by_name` KeyError, `return_type` không có trong lines response, "bogus" không bị chặn (400 test fail vì 201).

- [ ] **Step 3: Sửa serializers + views**

`backend/apps/inventory/transport/serializers/return_serializers.py`:

```python
class ReturnNoteLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ReturnNoteLine
        fields = [
            "id", "product", "product_sku", "product_name",
            "quantity", "condition_note", "return_type",
        ]


class ReturnNoteSerializer(serializers.ModelSerializer):
    lines = ReturnNoteLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ReturnNote
        fields = [
            "id", "code", "return_type", "status",
            "source_order", "shipping_code", "warehouse", "warehouse_name",
            "customer_name", "customer_phone", "source_note", "note",
            "created_by_name", "created_at", "posted_at", "lines",
        ]

    def get_created_by_name(self, obj) -> str:
        user = obj.created_by
        if user is None:
            return ""
        return user.display_name or user.username
```

`_ReturnLineInput` thêm:

```python
    return_type = serializers.ChoiceField(
        choices=ReturnType.values, required=False, allow_blank=True, default=""
    )
```

`return_views.py`: cả 6 chỗ `select_related("warehouse", "source_order")` đổi thành `select_related("warehouse", "source_order", "created_by")`.

- [ ] **Step 4: Chạy lại — PASS + không vỡ test cũ**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/ -q
```

Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/transport/ backend/apps/inventory/tests/test_return_api_line_type.py
git commit -m "feat(inventory): expose line return_type and creator name on return API"
```

---

### Task 4: Regen OpenAPI schema + FE types

**Files:**
- Modify: `backend/schema.yaml` (generated)
- Modify: `frontend/src/api/schema.d.ts` (generated)
- Modify: `frontend/src/features/inventory/returns-types.ts`

**Interfaces:**
- Produces: `ReturnNoteLineRow.return_type: ReturnType | ""`; `CreateReturnNotePayload`/`UpdateReturnNotePayload` lines nhận `return_type?: ReturnType`; `ReturnNoteRow.created_by_name: string`.

- [ ] **Step 1: Regen schema**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py spectacular --file schema.yaml
cd frontend && npm run gen:api && cd ..
```

Expected: `git diff` cho 2 file generated chỉ thay đổi quanh ReturnNote/ReturnNoteLine (+ created_by_name, return_type).

- [ ] **Step 2: Cập nhật returns-types.ts**

Trong `frontend/src/features/inventory/returns-types.ts`:

```typescript
export type ReturnNoteLineRow = {
  id: number;
  product: number;
  product_sku: string;
  product_name: string;
  quantity: number;
  condition_note: string;
  return_type: ReturnType | "";
};
```

`ReturnNoteRow` thêm `created_by_name: string;` (sau `note`). Trong `CreateReturnNotePayload` và `UpdateReturnNotePayload`, phần tử `lines` thành:

```typescript
  lines: { product_id: number; quantity: number; condition_note?: string; return_type?: ReturnType }[];
```

(Ở `UpdateReturnNotePayload` giữ `lines?:` optional như cũ.)

- [ ] **Step 3: Typecheck + commit**

```bash
cd frontend && npx tsc --noEmit && cd ..
git add backend/schema.yaml frontend/src/api/schema.d.ts frontend/src/features/inventory/returns-types.ts
git commit -m "chore(api): regen schema for return line type and creator name"
```

---

### Task 5: FE — module thuần `return-line-type.ts` (TDD)

**Files:**
- Create: `frontend/src/features/inventory/return-line-type.ts`
- Create: `frontend/src/features/inventory/__tests__/return-line-type.test.ts`

**Interfaces:**
- Consumes: types Task 4.
- Produces:
  - `RETURN_TYPE_OPTIONS: { value: ReturnType; label: string }[]` (4 loại, label tiếng Việt như RETURN_TYPE_LABELS hiện có);
  - `effectiveLineType(lineType: ReturnType | "", noteType: ReturnType | ""): ReturnType` — dòng > phiếu > `"delivery_returned"`;
  - `buildLineTypePayload(note: ReturnNoteRow, lineId: number, newType: ReturnType): UpdateReturnNotePayload` — replace toàn bộ lines, chỉ đổi loại của dòng `lineId`.

- [ ] **Step 1: Viết test fail**

Tạo `frontend/src/features/inventory/__tests__/return-line-type.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { buildLineTypePayload, effectiveLineType } from "../return-line-type";
import type { ReturnNoteRow } from "../returns-types";

const note = (over: Partial<ReturnNoteRow> = {}): ReturnNoteRow => ({
  id: 1,
  code: "TH-1",
  return_type: "delivery_returned",
  status: "draft",
  source_order: null,
  shipping_code: "",
  warehouse: 1,
  warehouse_name: "Kho",
  customer_name: "",
  customer_phone: "",
  source_note: "",
  note: "",
  created_by_name: "Kho Một",
  created_at: "2026-07-06T00:00:00Z",
  posted_at: null,
  lines: [
    { id: 11, product: 101, product_sku: "A", product_name: "SP A", quantity: 1, condition_note: "", return_type: "" },
    { id: 12, product: 102, product_sku: "B", product_name: "SP B", quantity: 2, condition_note: "vỡ hộp", return_type: "warranty" },
  ],
  ...over,
});

describe("effectiveLineType", () => {
  it("dòng có loại riêng thì dùng loại dòng", () => {
    expect(effectiveLineType("warranty", "delivery_returned")).toBe("warranty");
  });
  it("dòng trống kế thừa loại phiếu", () => {
    expect(effectiveLineType("", "defect")).toBe("defect");
  });
  it("cả hai trống → mặc định Hàng hoàn", () => {
    expect(effectiveLineType("", "")).toBe("delivery_returned");
  });
});

describe("buildLineTypePayload", () => {
  it("đổi loại đúng 1 dòng, giữ nguyên dòng khác (kể cả condition_note)", () => {
    const payload = buildLineTypePayload(note(), 11, "defect");
    expect(payload.lines).toEqual([
      { product_id: 101, quantity: 1, condition_note: undefined, return_type: "defect" },
      { product_id: 102, quantity: 2, condition_note: "vỡ hộp", return_type: "warranty" },
    ]);
  });
  it("dòng khác đang trống thì gửi return_type undefined (giữ kế thừa)", () => {
    const payload = buildLineTypePayload(note(), 12, "defect");
    expect(payload.lines?.[0].return_type).toBeUndefined();
    expect(payload.lines?.[1].return_type).toBe("defect");
  });
});
```

- [ ] **Step 2: Chạy, xác nhận FAIL** (module chưa tồn tại → tạo stub export sai/throw nếu vitest báo "No test suite", nhưng bình thường import error là đủ)

```bash
cd frontend && npx vitest run src/features/inventory/__tests__/return-line-type.test.ts
```

Expected: FAIL (cannot resolve `../return-line-type`).

- [ ] **Step 3: Implement**

Tạo `frontend/src/features/inventory/return-line-type.ts`:

```typescript
import type { ReturnNoteRow, ReturnType, UpdateReturnNotePayload } from "./returns-types";

export const RETURN_TYPE_OPTIONS: { value: ReturnType; label: string }[] = [
  { value: "delivery_returned", label: "Hàng hoàn" },
  { value: "external_return", label: "Trả hàng ngoài" },
  { value: "warranty", label: "Bảo hành" },
  { value: "defect", label: "Đổi lỗi" },
];

/** Loại hiệu lực của 1 dòng: dòng > loại chung phiếu > mặc định Hàng hoàn. */
export function effectiveLineType(
  lineType: ReturnType | "",
  noteType: ReturnType | "",
): ReturnType {
  return lineType || noteType || "delivery_returned";
}

/**
 * PATCH đổi loại 1 dòng: backend replace toàn bộ lines nên phải gửi lại đủ,
 * giữ nguyên quantity/condition_note/return_type các dòng khác.
 */
export function buildLineTypePayload(
  note: ReturnNoteRow,
  lineId: number,
  newType: ReturnType,
): UpdateReturnNotePayload {
  return {
    lines: note.lines.map((l) => ({
      product_id: l.product,
      quantity: l.quantity,
      condition_note: l.condition_note || undefined,
      return_type: l.id === lineId ? newType : l.return_type || undefined,
    })),
  };
}
```

- [ ] **Step 4: Chạy PASS**

```bash
cd frontend && npx vitest run src/features/inventory/__tests__/return-line-type.test.ts
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/return-line-type.ts frontend/src/features/inventory/__tests__/return-line-type.test.ts
git commit -m "feat(inventory-fe): return line type helpers"
```

---

### Task 6: FE — ReturnNoteDetailDialog: select per dòng, Loại chung, Người tạo, responsive

**Files:**
- Modify: `frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx`
- Create: `frontend/src/features/inventory/__tests__/ReturnNoteDetailDialog.test.tsx`

**Interfaces:**
- Consumes: `RETURN_TYPE_OPTIONS`, `effectiveLineType`, `buildLineTypePayload` (Task 5); `useReturnNote`, `useUpdateReturn` (sẵn có, `frontend/src/features/inventory/returns-queries.ts`).
- Produces: UI hoàn chỉnh — không task nào sau phụ thuộc.

- [ ] **Step 1: Viết component test fail**

Tạo `frontend/src/features/inventory/__tests__/ReturnNoteDetailDialog.test.tsx`. Mock `fetchJson` (pattern: `vi.mock("@/services/http")`); dữ liệu note nháp 2 dòng (1 dòng trống loại, 1 dòng warranty), `return_type: "delivery_returned"`, `created_by_name: "Kho Một"`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { fetchJson } from "@/services/http";
import { ReturnNoteDetailDialog } from "@/features/inventory/components/ReturnNoteDetailDialog";

vi.mock("@/services/http", () => ({ fetchJson: vi.fn() }));

const mockNote = {
  id: 7,
  code: "TH20260706-0001",
  return_type: "delivery_returned",
  status: "draft",
  source_order: 529408,
  shipping_code: "GYX44ELX",
  warehouse: 1,
  warehouse_name: "Đại Mỗ 1",
  customer_name: "Khach",
  customer_phone: "0900000001",
  source_note: "",
  note: "",
  created_by_name: "Kho Một",
  created_at: "2026-07-06T00:00:00Z",
  posted_at: null,
  lines: [
    { id: 11, product: 101, product_sku: "BNC02", product_name: "Bồn ngâm", quantity: 1, condition_note: "", return_type: "" },
    { id: 12, product: 102, product_sku: "MAY01", product_name: "Máy đo", quantity: 1, condition_note: "", return_type: "warranty" },
  ],
};

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReturnNoteDetailDialog returnId={7} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(fetchJson).mockReset();
  vi.mocked(fetchJson).mockResolvedValue(mockNote);
});

describe("ReturnNoteDetailDialog", () => {
  it("hiển thị người tạo phiếu", async () => {
    renderDialog();
    expect(await screen.findByText("Kho Một")).toBeInTheDocument();
  });

  it("mỗi dòng hàng nháp có ô chọn loại, giá trị = loại hiệu lực", async () => {
    renderDialog();
    const selects = await screen.findAllByRole("combobox", { name: /loại hoàn/i });
    expect(selects).toHaveLength(2);
    expect(selects[0]).toHaveTextContent("Hàng hoàn"); // dòng trống kế thừa loại chung
    expect(selects[1]).toHaveTextContent("Bảo hành");
  });

  it("đổi loại 1 dòng gửi PATCH với đủ lines", async () => {
    renderDialog();
    const user = userEvent.setup();
    const selects = await screen.findAllByRole("combobox", { name: /loại hoàn/i });
    await user.click(selects[0]);
    await user.click(await screen.findByRole("option", { name: "Đổi lỗi" }));
    await waitFor(() => {
      const patchCall = vi
        .mocked(fetchJson)
        .mock.calls.find(([, init]) => (init as RequestInit)?.method === "PATCH");
      expect(patchCall).toBeDefined();
      const body = JSON.parse((patchCall![1] as RequestInit).body as string);
      expect(body.lines).toEqual([
        { product_id: 101, quantity: 1, return_type: "defect" },
        { product_id: 102, quantity: 1, return_type: "warranty" },
      ]);
    });
  });
});
```

Lưu ý cho người implement: `condition_note: undefined` bị JSON.stringify lược bỏ — assert như trên là đúng. Nếu shadcn Select trong jsdom cần `aria-label`, đặt `aria-label={`Loại hoàn ${l.product_sku}`}` trên `SelectTrigger` của từng dòng để query `getAllByRole("combobox", { name: /loại hoàn/i })` hoạt động. Nếu Radix Select cần `pointer-events` hack trong jsdom, xem các test dialog hiện có trong repo làm mẫu (grep `findByRole("option"` trong `frontend/src`).

- [ ] **Step 2: Chạy, xác nhận FAIL**

```bash
cd frontend && npx vitest run src/features/inventory/__tests__/ReturnNoteDetailDialog.test.tsx
```

Expected: FAIL — "Kho Một" không render, không có combobox "Loại hoàn".

- [ ] **Step 3: Sửa dialog**

Trong `ReturnNoteDetailDialog.tsx`:

(a) Import thêm:

```tsx
import {
  RETURN_TYPE_OPTIONS,
  buildLineTypePayload,
  effectiveLineType,
} from "@/features/inventory/return-line-type";
```

(b) Nhãn ô "Loại: " → "Loại chung: "; value select loại chung: `value={note.return_type || "delivery_returned"}` (bỏ `|| undefined`); các `SelectItem` giữ nguyên nhưng render từ `RETURN_TYPE_OPTIONS.map(...)` để khỏi lặp label. Thay `RETURN_TYPE_LABELS` cục bộ bằng lookup từ `RETURN_TYPE_OPTIONS` (hoặc giữ — không bắt buộc).

(c) Header grid: `className="grid grid-cols-2 gap-2"` → `className="grid grid-cols-1 gap-2 sm:grid-cols-2"`. Thêm sau khối SĐT:

```tsx
              <p>
                <span className="text-muted-foreground">Người tạo: </span>
                {note.created_by_name || "—"}
              </p>
```

(d) Handler đổi loại dòng:

```tsx
  async function handleChangeLineType(lineId: number, newType: string) {
    if (returnId == null || !note) return;
    try {
      await updateMutation.mutateAsync({
        id: returnId,
        payload: buildLineTypePayload(note, lineId, newType as ReturnType),
      });
      toast.success("Đã cập nhật loại cho dòng hàng.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cập nhật loại dòng thất bại.");
    }
  }
```

(e) Bảng dòng hàng: desktop thêm cột "Loại hoàn"; mobile chuyển card. Thay khối `<table>` hiện tại (dòng 204-221) bằng:

```tsx
                <>
                  {/* Desktop: bảng */}
                  <table data-slot="table" className="hidden w-full text-sm sm:table">
                    <thead className="text-left text-muted-foreground">
                      <tr>
                        <th className="px-2 py-1">SKU</th>
                        <th className="px-2 py-1">Tên</th>
                        <th className="px-2 py-1">SL</th>
                        <th className="px-2 py-1">Loại hoàn</th>
                      </tr>
                    </thead>
                    <tbody>
                      {note.lines.map((l) => (
                        <tr key={l.id} className="border-t border-border">
                          <td className="px-2 py-1 font-medium">{l.product_sku}</td>
                          <td className="px-2 py-1">{l.product_name}</td>
                          <td className="px-2 py-1">{l.quantity}</td>
                          <td className="px-2 py-1">
                            <LineTypeCell
                              line={l}
                              noteType={note.return_type}
                              editable={note.status === "draft"}
                              onChange={handleChangeLineType}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {/* Mobile: card từng dòng */}
                  <div className="space-y-2 sm:hidden">
                    {note.lines.map((l) => (
                      <div key={l.id} className="rounded border border-border px-2 py-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <p className="min-w-0 truncate text-sm font-medium">{l.product_sku}</p>
                          <span className="text-sm">SL: {l.quantity}</span>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">{l.product_name}</p>
                        <div className="mt-1.5">
                          <LineTypeCell
                            line={l}
                            noteType={note.return_type}
                            editable={note.status === "draft"}
                            onChange={handleChangeLineType}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </>
```

(f) Component con trong cùng file (dưới cùng, trước hoặc sau component chính):

```tsx
function LineTypeCell({
  line,
  noteType,
  editable,
  onChange,
}: {
  line: ReturnNoteRow["lines"][number];
  noteType: ReturnNoteRow["return_type"];
  editable: boolean;
  onChange: (lineId: number, newType: string) => void;
}) {
  const effective = effectiveLineType(line.return_type, noteType);
  const label = RETURN_TYPE_OPTIONS.find((o) => o.value === effective)?.label ?? effective;
  if (!editable) return <span>{label}</span>;
  return (
    <Select value={effective} onValueChange={(v) => onChange(line.id, v)}>
      <SelectTrigger
        className="h-8 w-full min-w-32"
        aria-label={`Loại hoàn ${line.product_sku}`}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {RETURN_TYPE_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

Cần import `ReturnNoteRow` type: `import type { ReturnNoteRow, ReturnType } from "@/features/inventory/returns-types";`.

- [ ] **Step 4: Chạy test PASS + toàn bộ test inventory FE**

```bash
cd frontend && npx vitest run src/features/inventory
```

Expected: PASS toàn bộ (36 test cũ + mới).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx frontend/src/features/inventory/__tests__/ReturnNoteDetailDialog.test.tsx
git commit -m "feat(inventory-fe): per-line return type, creator, mobile layout in return detail"
```

---

### Task 7: FE — mặc định Hàng hoàn ở 2 luồng tạo phiếu

**Files:**
- Modify: `frontend/src/features/inventory/scan/scan-return-rows.ts` (`buildReturnPayloads`)
- Modify: `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts`
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`

**Interfaces:**
- Consumes: `CreateReturnNotePayload.return_type` (Task 4).
- Produces: mọi phiếu tạo mới mang `return_type: "delivery_returned"` (trừ khi user đổi ở CreateReturnDialog).

- [ ] **Step 1: Cập nhật test scan-return-rows (fail trước)**

Trong `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts`, thêm vào describe của `buildReturnPayloads`:

```typescript
  it("mọi payload mang loại mặc định Hàng hoàn", () => {
    const rows: ScanReturnRow[] = [
      { kind: "matched", code: "VD-1", order: order() },
      { kind: "unmatched", code: "VD-404" },
    ];
    const payloads = buildReturnPayloads(rows, 3);
    expect(payloads[0].return_type).toBe("delivery_returned");
    expect(payloads[1].return_type).toBe("delivery_returned");
  });
```

Chạy: `cd frontend && npx vitest run src/features/inventory/scan/__tests__/scan-return-rows.test.ts` → Expected: FAIL (return_type undefined).

- [ ] **Step 2: Sửa buildReturnPayloads**

Trong `scan-return-rows.ts`, cả 2 nhánh payload thêm `return_type: "delivery_returned",`:

```typescript
  return rows.map((r) =>
    r.kind === "matched"
      ? {
          return_type: "delivery_returned",
          warehouse_id: warehouseId,
          shipping_code: r.code,
          source_order_id: r.order.order_id,
          customer_name: r.order.customer_name,
          customer_phone: r.order.customer_phone,
          lines: mergedLines(r.order),
        }
      : {
          return_type: "delivery_returned",
          warehouse_id: warehouseId,
          shipping_code: r.code,
          lines: [],
        },
  );
```

Chạy lại test → PASS.

- [ ] **Step 3: CreateReturnDialog thêm select "Loại chung" mặc định Hàng hoàn**

Trong `CreateReturnDialog.tsx`:
- State: `const [returnType, setReturnType] = useState<ReturnType>("delivery_returned");` (import `ReturnType` + `RETURN_TYPE_OPTIONS` từ `@/features/inventory/return-line-type` và type từ returns-types).
- `resetForm()` thêm `setReturnType("delivery_returned");`.
- `handleSubmit`: payload thêm `return_type: returnType,` (đặt ngay sau `warehouse_id`).
- UI: ngay sau khối Select "Kho nhận" (kết thúc ~dòng 272), thêm:

```tsx
          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Loại chung</label>
            <Select value={returnType} onValueChange={(v) => setReturnType(v as ReturnType)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RETURN_TYPE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
```

- [ ] **Step 4: Chạy test + typecheck**

```bash
cd frontend && npx vitest run src/features/inventory && npx tsc --noEmit
```

Expected: PASS / exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/scan-return-rows.ts frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts frontend/src/features/inventory/pages/CreateReturnDialog.tsx
git commit -m "feat(inventory-fe): default return type to delivery_returned on create flows"
```

---

### Task 8: Verify toàn cục + push + MR

**Files:** không sửa code (chỉ chạy kiểm tra; sửa nếu đỏ).

- [ ] **Step 1: BE full inventory + ruff**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/ apps/orders/tests/test_dispatch_stock_cycle.py -q
uv run --no-project --with ruff ruff check backend/apps/inventory
```

Expected: PASS / no issues. (`uv` ở `/c/Users/MYPC/.local/bin/uv`.)

- [ ] **Step 2: FE full suite + lint + typecheck**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src/features/inventory
```

Expected: các test inventory pass. LƯU Ý baseline: `OrderFormDialog` + 2 test mobile-viewport vốn FAIL trên master sạch (memory `known-failing-baseline-tests`) — không phải regression, không sửa.

- [ ] **Step 3: Push + MR link**

```bash
git push -u origin feat/return-line-type
```

Gửi user link tạo MR mà GitLab in ra (dạng `https://gitlab.com/desi3512912/desi-order/-/merge_requests/new?...`).

---

## Self-review đã chạy

- Spec coverage: per-line type (Task 1-3, 5-6), mặc định Hàng hoàn (Task 6 loại chung fallback + Task 7 hai luồng tạo), ghi sổ per-line + RETURNED khi có dòng Hàng hoàn (Task 2), created_by_name (Task 3, 6), responsive mobile (Task 6e), regen schema (Task 4). Mục "Nhiều loại trong danh sách" của spec: `ReturnCard` hiện KHÔNG được import ở page nào (đã kiểm tra), danh sách Phiếu kho dùng endpoint ledger không có `lines` → không có bề mặt nào đủ dữ liệu; bỏ qua đúng theo điều kiện "chỉ nơi đã có sẵn lines" của spec.
- Type consistency: `effectiveLineType`/`buildLineTypePayload`/`RETURN_TYPE_OPTIONS` thống nhất Task 5→6→7; `created_by_name` thống nhất Task 3→4→6.
- Placeholders: không còn.
