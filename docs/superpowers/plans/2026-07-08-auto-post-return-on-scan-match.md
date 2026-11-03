# Auto-post Return Note on Scan Match — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi bấm "Xác nhận" trong luồng quét trả hàng, phiếu khớp mã vận đơn được ghi sổ tự động (POSTED) + đơn nguồn set RETURNED ("đã chuyển hoàn"), thay vì tạo nháp rồi ghi sổ tay.

**Architecture:** Thêm service atomic `create_and_post_return` (bọc `create_return` + `post_return` trong 1 transaction — lỗi thì rollback toàn bộ). Endpoint tạo phiếu nhận cờ `auto_post`; FE luồng quét gửi `auto_post: true` cho dòng matched. Trạng thái "đã chuyển hoàn" + push KiotViet đã có sẵn trong `post_return`, không thêm mới.

**Tech Stack:** Django REST Framework (Python 3.12), React + TypeScript + Vitest, React Query.

## Global Constraints

- Backend layering: `transport → business (service) → storage`. Không phá ranh giới module: import `apps.orders` phải là **local import bên trong hàm** (giữ như code hiện tại trong `return_note_service.py`).
- Không migration DB (chỉ thêm field serializer, không đổi model).
- Backend test chạy qua Docker (xem lệnh ở mỗi task); dùng `pytest`, KHÔNG `manage.py test`; thêm `-e POSTGRES_DB=desi_<unique>` để có test DB sạch.
- Frontend: GET data mới dùng React Query; không đổi convention. FE test: `npm run test` (vitest) trong `frontend/`.
- `auto_post` mặc định `false` → mọi caller cũ giữ nguyên hành vi.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng Co-Authored-By.

---

### Task 1: Backend service `create_and_post_return`

**Files:**
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (thêm method sau `post_return`, quanh dòng 187)
- Test (create): `backend/apps/inventory/tests/test_create_and_post_return.py`

**Interfaces:**
- Consumes: `ReturnNoteService.create_return(...)`, `ReturnNoteService.post_return(return_note_id, user_id)` (đã có).
- Produces: `ReturnNoteService.create_and_post_return(*, user_id=None, **create_kwargs) -> ReturnNote` — trả về phiếu đã POSTED. Raise `ValueError` (rollback toàn bộ) nếu create hoặc post lỗi. `create_kwargs` = đúng bộ tham số của `create_return` (`return_type`, `warehouse_id`, `lines`, `source_order_id`, `shipping_code`, `customer_name`, `customer_phone`, `source_note`, `note`, `scan_batch_id`).

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/inventory/tests/test_create_and_post_return.py`:

```python
"""create_and_post_return: ghi sổ ngay khi tạo phiếu (luồng quét khớp mã) —
atomic: post lỗi thì không phiếu nào tồn tại, status đơn không đổi."""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, StockItem
from apps.orders.models import Order, OrderStatus, Product
from apps.warehouses.models import Warehouse


class CreateAndPostReturnTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-CAP", name="Kho CAP")
        self.p1 = Product.objects.create(name="SP1", sku="SKU-CAP1")
        self.channel = Channel.objects.create(code="ch-cap", name="CAP Channel")
        self.customer = Customer.objects.create(
            full_name="Khach CAP",
            phone_raw="0912345671",
            phone_normalized="0912345671",
            code="CUST-CAP",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach CAP",
            phone="0912345671",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _qty(self, product, state):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state=state
        ).first()
        return item.quantity if item else 0

    def test_happy_path_posts_note_marks_order_and_moves_stock(self):
        rn = ReturnNoteService.create_and_post_return(
            return_type="delivery_returned",
            warehouse_id=self.warehouse.id,
            lines=[{"product_id": self.p1.id, "quantity": 2}],
            source_order_id=self.order.id,
        )
        self.assertEqual(rn.status, "posted")
        self.assertIsNotNone(rn.posted_at)
        self.assertEqual(self._qty(self.p1, "sellable"), 2)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_rollback_on_post_failure_creates_nothing(self):
        before = int(self.order.status)
        # return_type="" ở cấp phiếu + dòng không có loại → post_return raise
        # "chưa xác định loại trả hàng" → transaction rollback toàn bộ.
        with self.assertRaises(ValueError):
            ReturnNoteService.create_and_post_return(
                return_type="",
                warehouse_id=self.warehouse.id,
                lines=[{"product_id": self.p1.id, "quantity": 1}],
                source_order_id=self.order.id,
            )
        self.assertEqual(ReturnNote.objects.count(), 0)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), before)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_cap1 \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend:/app" -w /app \
  desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_create_and_post_return.py -q'
```

Expected: FAIL — `AttributeError: type object 'ReturnNoteService' has no attribute 'create_and_post_return'`.
(Nếu network/redis khác: `docker ps` để lấy network + REDIS_PASSWORD đúng — xem memory running-backend-tests.)

- [ ] **Step 3: Implement `create_and_post_return`**

Trong `return_note_service.py`, thêm ngay sau `post_return` (kết thúc ở dòng ~187, trước `update_draft`):

```python
    @staticmethod
    @transaction.atomic
    def create_and_post_return(
        *, user_id: int | None = None, **create_kwargs
    ) -> ReturnNote:
        """Tạo + ghi sổ phiếu trả trong 1 transaction (luồng quét khớp mã).

        Lỗi ở create hoặc post → rollback toàn bộ: không phiếu, không dòng,
        không movement, không đổi status đơn. Tái dùng nguyên post_return
        (gồm mark_order_returned + push KiotViet on_commit).
        """
        rn = ReturnNoteService.create_return(user_id=user_id, **create_kwargs)
        return ReturnNoteService.post_return(return_note_id=rn.id, user_id=user_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Lệnh như Step 2 (đổi `-e POSTGRES_DB=desi_cap2` để DB sạch).
Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add backend/apps/inventory/application/services/return_note_service.py \
        backend/apps/inventory/tests/test_create_and_post_return.py
git commit -m "feat: add create_and_post_return atomic service for scan matches"
```

---

### Task 2: Backend endpoint `auto_post` flag

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py` (`CreateReturnNoteSerializer`, quanh dòng 50-68)
- Modify: `backend/apps/inventory/transport/views/return_views.py` (nhánh POST của `list_create_returns`, dòng 90-117)
- Test (create): `backend/apps/inventory/tests/test_return_auto_post.py`

**Interfaces:**
- Consumes: `ReturnNoteService.create_and_post_return(...)` (Task 1), `ReturnNoteService.create_return(...)`.
- Produces: `POST /api/inventory/returns` nhận thêm `auto_post: bool` (mặc định `false`). `true` → phiếu trả về `status="posted"`; lỗi nghiệp vụ → 400 `{detail}` và không tạo phiếu.

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/inventory/tests/test_return_auto_post.py`:

```python
"""Endpoint tạo phiếu trả với auto_post=true: ghi sổ ngay + set đơn RETURNED,
lỗi thì rollback (không tạo phiếu). auto_post=false giữ hành vi nháp cũ."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.models import ReturnNote
from apps.orders.models import Order, OrderStatus, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class ReturnAutoPostTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_auto_post")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WAP1", name="Kho AutoPost")
        self.product = Product.objects.create(name="SP AP", sku="AP-1")
        self.channel = Channel.objects.create(code="ch-ap", name="AP Channel")
        self.customer = Customer.objects.create(
            full_name="Khach AP",
            phone_raw="0912345672",
            phone_normalized="0912345672",
            code="CUST-AP",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach AP",
            phone="0912345672",
            total_amount="100000",
            status=OrderStatus.AWAITING_SHIPMENT,
            shipping_code="VNAP001",
            fulfillment_warehouse=self.wh,
        )

    def _post(self, extra):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "source_order_id": self.order.id,
                "shipping_code": "VNAP001",
                "lines": [{"product_id": self.product.id, "quantity": 1}],
                **extra,
            },
            format="json",
        )

    def test_auto_post_true_posts_and_marks_order_returned(self):
        resp = self._post({"return_type": "delivery_returned", "auto_post": True})
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "posted"
        self.order.refresh_from_db()
        assert int(self.order.status) == int(OrderStatus.RETURNED)

    def test_auto_post_failure_rolls_back_creates_nothing(self):
        before = int(self.order.status)
        # return_type rỗng + dòng không loại → post raise → 400, không tạo phiếu.
        resp = self._post({"return_type": "", "auto_post": True})
        assert resp.status_code == 400, resp.json()
        assert ReturnNote.objects.count() == 0
        self.order.refresh_from_db()
        assert int(self.order.status) == before

    def test_default_no_auto_post_creates_draft(self):
        resp = self._post({"return_type": "delivery_returned"})
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "draft"
        self.order.refresh_from_db()
        assert int(self.order.status) != int(OrderStatus.RETURNED)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_ap1 \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend:/app" -w /app \
  desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_return_auto_post.py -q'
```

Expected: FAIL — `test_auto_post_true` trả 201 nhưng `status == "draft"` (chưa có auto_post → chỉ tạo nháp); `test_auto_post_failure` tạo phiếu draft thay vì 400.

- [ ] **Step 3: Add `auto_post` to serializer**

Trong `return_serializers.py`, thêm vào `CreateReturnNoteSerializer` (sau dòng `lines = ...`, dòng 68):

```python
    # Luồng quét khớp mã: ghi sổ ngay khi tạo (create_and_post_return).
    auto_post = serializers.BooleanField(required=False, default=False)
```

- [ ] **Step 4: Wire endpoint**

Trong `return_views.py`, thay khối POST (dòng 95-111) bằng:

```python
    data = serializer.validated_data
    auto_post = data.pop("auto_post", False)
    create_kwargs = dict(
        return_type=data["return_type"],
        warehouse_id=data["warehouse_id"],
        lines=list(data["lines"]),
        source_order_id=data.get("source_order_id"),
        shipping_code=data.get("shipping_code", ""),
        customer_name=data.get("customer_name", ""),
        customer_phone=data.get("customer_phone", ""),
        source_note=data.get("source_note", ""),
        note=data.get("note", ""),
        user_id=request.user.id,
        scan_batch_id=data.get("scan_batch_id"),
    )
    try:
        if auto_post:
            rn = ReturnNoteService.create_and_post_return(**create_kwargs)
        else:
            rn = ReturnNoteService.create_return(**create_kwargs)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
```

(Giữ nguyên phần re-fetch + `Response(ReturnNoteSerializer(rn).data, status=201)` ngay dưới.)

- [ ] **Step 5: Run tests to verify they pass**

Lệnh như Step 2 (đổi `-e POSTGRES_DB=desi_ap2`).
Expected: PASS — 3 passed.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add backend/apps/inventory/transport/serializers/return_serializers.py \
        backend/apps/inventory/transport/views/return_views.py \
        backend/apps/inventory/tests/test_return_auto_post.py
git commit -m "feat: add auto_post flag to create return endpoint"
```

---

### Task 3: Frontend payload type + `buildReturnPayloads`

**Files:**
- Modify: `frontend/src/features/inventory/returns-types.ts` (`CreateReturnNotePayload`, dòng 40-56)
- Modify: `frontend/src/features/inventory/scan/scan-return-rows.ts` (`buildReturnPayloads`, dòng 37-54)
- Test (modify): `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts`

**Interfaces:**
- Consumes: `CreateReturnNotePayload` (existing).
- Produces: `buildReturnPayloads(rows, warehouseId, scanBatchId?)` → mỗi payload matched thêm `auto_post: true`. `CreateReturnNotePayload` có field mới `auto_post?: boolean`.

- [ ] **Step 1: Update the failing test**

Trong `scan-return-rows.test.ts`, sửa assertion `toEqual` (dòng 56-65) để gồm `auto_post: true`:

```typescript
    expect(p).toEqual({
      return_type: "delivery_returned",
      warehouse_id: 7,
      shipping_code: "VD-1",
      source_order_id: 1,
      customer_name: "Khách A",
      customer_phone: "0900000000",
      scan_batch_id: null,
      auto_post: true,
      lines: [{ product_id: 10, quantity: 5 }],
    });
```

Và thêm một test khẳng định rõ cờ (sau test "gắn scan_batch_id", dòng ~72):

```typescript
  it("gắn auto_post=true để ghi sổ ngay cho dòng khớp đơn", () => {
    const rows: ScanReturnRow[] = [{ kind: "matched", code: "VD-1", order: order() }];
    const [p] = buildReturnPayloads(rows, 7);
    expect(p.auto_post).toBe(true);
  });
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run test -- scan-return-rows
```

Expected: FAIL — payload thiếu `auto_post` (toEqual mismatch; `p.auto_post` là `undefined`).

- [ ] **Step 3: Add `auto_post` to the payload type**

Trong `returns-types.ts`, thêm vào `CreateReturnNotePayload` (sau `scan_batch_id?`, dòng 55):

```typescript
  /** true = ghi sổ ngay khi tạo (luồng quét khớp mã vận đơn). */
  auto_post?: boolean;
```

- [ ] **Step 4: Set `auto_post: true` in `buildReturnPayloads`**

Trong `scan-return-rows.ts`, thêm dòng vào object map (sau `scan_batch_id: scanBatchId,`, dòng 51):

```typescript
      auto_post: true,
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run test -- scan-return-rows
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add frontend/src/features/inventory/returns-types.ts \
        frontend/src/features/inventory/scan/scan-return-rows.ts \
        frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts
git commit -m "feat: send auto_post on scan return matched payloads"
```

---

### Task 4: Frontend `ScanReturnDialog` UX (ghi sổ + toast)

**Files:**
- Create: `frontend/src/features/inventory/scan/scan-return-result.ts` (logic thuần: nhãn kết quả dòng + tóm tắt toast)
- Modify: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx` (`RowResult` dòng 31, khối tạo phiếu trong `handleConfirm` dòng 168-227, hiển thị kết quả dòng 346-356)
- Test (create): `frontend/src/features/inventory/scan/__tests__/scan-return-result.test.ts`

**Interfaces:**
- Consumes: `useCreateReturn()` → `mutateAsync(payload)` trả `ReturnNoteRow` (có `status`, `code`).
- Produces:
  - `ScanRowResult = { ok: boolean; noteCode?: string; posted?: boolean; error?: string }`
  - `scanRowResultLabel(res: ScanRowResult): string` — nhãn hiển thị 1 dòng.
  - `scanConfirmSummary(createdCount, pendingCount, failedCount): string` — câu toast tổng kết.
- Lý do tách hàm thuần: tránh render radix `Select` trong jsdom (pointer events hay lỗi); theo đúng pattern `scan-return-rows.ts`.

- [ ] **Step 1: Write the failing unit test**

Create `frontend/src/features/inventory/scan/__tests__/scan-return-result.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { scanRowResultLabel, scanConfirmSummary } from "../scan-return-result";

describe("scanRowResultLabel", () => {
  it("phiếu đã ghi sổ (auto_post) → 'Đã ghi sổ {code}'", () => {
    expect(
      scanRowResultLabel({ ok: true, noteCode: "TH20260708-0001", posted: true }),
    ).toBe("✓ Đã ghi sổ TH20260708-0001");
  });

  it("phiếu nháp (không auto_post) → '{code}'", () => {
    expect(scanRowResultLabel({ ok: true, noteCode: "TH20260708-0002" })).toBe(
      "✓ TH20260708-0002",
    );
  });

  it("mã chờ khớp → 'Đã ghi nhận — chờ khớp'", () => {
    expect(scanRowResultLabel({ ok: true, noteCode: "chờ khớp" })).toBe(
      "✓ Đã ghi nhận — chờ khớp",
    );
  });

  it("lỗi → '✗ {error}'", () => {
    expect(scanRowResultLabel({ ok: false, error: "đơn đã hoàn kho" })).toBe(
      "✗ đơn đã hoàn kho",
    );
  });
});

describe("scanConfirmSummary", () => {
  it("không lỗi → không thêm hậu tố lỗi", () => {
    expect(scanConfirmSummary(2, 1, 0)).toBe("Đã ghi sổ 2 phiếu — 1 mã chờ khớp");
  });

  it("có lỗi → thêm ' — N lỗi'", () => {
    expect(scanConfirmSummary(2, 0, 1)).toBe("Đã ghi sổ 2 phiếu — 0 mã chờ khớp — 1 lỗi");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run test -- scan-return-result
```

Expected: FAIL — `Failed to resolve import "../scan-return-result"`.

- [ ] **Step 3: Create the pure logic module**

Create `frontend/src/features/inventory/scan/scan-return-result.ts`:

```typescript
export type ScanRowResult = {
  ok: boolean;
  noteCode?: string;
  posted?: boolean;
  error?: string;
};

/** Nhãn kết quả cho 1 dòng quét (đã ghi sổ / nháp / chờ khớp / lỗi). */
export function scanRowResultLabel(res: ScanRowResult): string {
  if (!res.ok) return `✗ ${res.error}`;
  if (res.noteCode === "chờ khớp") return "✓ Đã ghi nhận — chờ khớp";
  if (res.posted) return `✓ Đã ghi sổ ${res.noteCode}`;
  return `✓ ${res.noteCode}`;
}

/** Câu toast tóm tắt sau khi Xác nhận cả đợt. */
export function scanConfirmSummary(
  createdCount: number,
  pendingCount: number,
  failedCount: number,
): string {
  const failSuffix = failedCount > 0 ? ` — ${failedCount} lỗi` : "";
  return `Đã ghi sổ ${createdCount} phiếu — ${pendingCount} mã chờ khớp${failSuffix}`;
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run test -- scan-return-result
```

Expected: PASS.

- [ ] **Step 5: Wire the module into `ScanReturnDialog.tsx`**

Thêm import (cạnh import `scan-return-rows`, dòng 25):

```typescript
import { scanRowResultLabel, scanConfirmSummary } from "./scan-return-result";
import type { ScanRowResult } from "./scan-return-result";
```

Thay khai báo type `RowResult` (dòng 31) bằng dùng type chung:

```typescript
type RowResult = ScanRowResult;
```

Trong `handleConfirm`, chỗ tạo phiếu thành công (dòng 181-182), set `posted`:

```typescript
        const note = await createReturn.mutateAsync(payload);
        next[row.code] = { ok: true, noteCode: note.code, posted: note.status === "posted" };
```

Thay khối tính toast tổng kết (dòng 224-226):

```typescript
    const createdCount = matched.filter((r) => next[r.code]?.ok).length;
    const failedCount = matched.filter((r) => next[r.code] && !next[r.code].ok).length;
    const pendingCount = unmatched.filter((r) => next[r.code]?.ok).length;
    toast.success(scanConfirmSummary(createdCount, pendingCount, failedCount));
```

Thay khối hiển thị kết quả dòng (dòng 347-355) — dùng helper thay vì JSX lồng:

```tsx
                        {res && (
                          <span className={res.ok ? "text-green-600" : "text-red-600"}>
                            {scanRowResultLabel(res)}
                          </span>
                        )}
```

- [ ] **Step 6: Verify no regression + typecheck**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run typecheck && npm run test -- scan-return-result scan-cross-kind-pending scan-return-rows
```

Expected: typecheck sạch; các test scan pass (luồng unmatched không đổi).

- [ ] **Step 7: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add frontend/src/features/inventory/scan/scan-return-result.ts \
        frontend/src/features/inventory/scan/ScanReturnDialog.tsx \
        frontend/src/features/inventory/scan/__tests__/scan-return-result.test.ts
git commit -m "feat: show posted status on scan return confirm"
```

---

### Task 5: Sync OpenAPI schema types (hygiene, optional)

**Files:**
- Regenerate: `frontend/src/api/schema.d.ts`
- (Backend schema file: `backend/schema.yaml`)

**Interfaces:**
- Consumes: `npm run gen:api` (đọc `../backend/schema.yaml`). Luồng returns dùng type tay (`returns-types.ts`), KHÔNG phụ thuộc `schema.d.ts` — task này chỉ giữ schema đồng bộ, không bắt buộc để tính năng chạy.

- [ ] **Step 1: Regenerate backend schema.yaml**

```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  --network desi-order_default \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend:/app" -w /app \
  desi-order-web-test:latest \
  python manage.py spectacular --file schema.yaml
```

- [ ] **Step 2: Regenerate FE types**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run gen:api
```

- [ ] **Step 3: Verify diff is scoped**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git diff --stat frontend/src/api/schema.d.ts backend/schema.yaml
```

Nếu diff chỉ liên quan `auto_post` / return endpoint → commit. Nếu diff lớn/nhiễu do schema drift không liên quan → **bỏ qua task này** (revert `git checkout -- frontend/src/api/schema.d.ts backend/schema.yaml`); tính năng vẫn hoạt động vì FE dùng type tay.

- [ ] **Step 4: Commit (nếu diff sạch)**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add frontend/src/api/schema.d.ts backend/schema.yaml
git commit -m "chore: regenerate openapi schema for auto_post field"
```

---

### Task 6: Backfill command — ghi sổ phiếu nháp khớp đơn đang tồn

**Files:**
- Create: `backend/apps/inventory/management/commands/post_matched_return_drafts.py`
- Test (create): `backend/apps/inventory/tests/test_post_matched_return_drafts_command.py`

**Interfaces:**
- Consumes: `ReturnNoteService.post_return(return_note_id, user_id=None)` (đã có), `ReturnNote`, `ReturnStatus`, `ReturnType`.
- Produces: `python manage.py post_matched_return_drafts [--dry-run] [--limit N]`. Ghi sổ mọi phiếu `DRAFT` + `source_order` không NULL + `return_type=delivery_returned` + có dòng. Guard chặn → skip + log, không dừng.
- Phụ thuộc: chỉ `post_return` (độc lập với Task 1-5); có thể làm bất kỳ lúc nào sau khi worktree sẵn sàng.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/inventory/tests/test_post_matched_return_drafts_command.py`:

```python
"""Backfill command: ghi sổ phiếu nháp khớp đơn (delivery_returned, có dòng);
bỏ qua phiếu chờ khớp 0 dòng và phiếu không có source_order."""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnStatus
from apps.orders.models import Order, OrderStatus, Product
from apps.warehouses.models import Warehouse


class PostMatchedReturnDraftsCommandTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="WBF1", name="Kho Backfill")
        self.product = Product.objects.create(name="SP BF", sku="BF-1")
        self.channel = Channel.objects.create(code="ch-bf", name="BF Channel")
        self.customer = Customer.objects.create(
            full_name="Khach BF",
            phone_raw="0912345673",
            phone_normalized="0912345673",
            code="CUST-BF",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach BF",
            phone="0912345673",
            total_amount="100000",
            fulfillment_warehouse=self.wh,
        )

    def _matched_draft(self):
        return ReturnNoteService.create_return(
            return_type="delivery_returned",
            warehouse_id=self.wh.id,
            lines=[{"product_id": self.product.id, "quantity": 1}],
            source_order_id=self.order.id,
        )

    def test_dry_run_posts_nothing(self):
        rn = self._matched_draft()
        out = StringIO()
        call_command("post_matched_return_drafts", "--dry-run", stdout=out)
        rn.refresh_from_db()
        self.assertEqual(rn.status, ReturnStatus.DRAFT)

    def test_posts_matched_draft_and_marks_order(self):
        rn = self._matched_draft()
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        rn.refresh_from_db()
        self.assertEqual(rn.status, ReturnStatus.POSTED)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_skips_unmatched_zero_line_draft(self):
        # phiếu chờ khớp: có shipping_code, không source_order, 0 dòng
        pending = ReturnNoteService.create_return(
            return_type="delivery_returned",
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VNBF-PENDING",
        )
        out = StringIO()
        call_command("post_matched_return_drafts", stdout=out)
        pending.refresh_from_db()
        self.assertEqual(pending.status, ReturnStatus.DRAFT)  # không đụng tới
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_bf1 \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend:/app" -w /app \
  desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_post_matched_return_drafts_command.py -q'
```

Expected: FAIL — `CommandError: Unknown command: 'post_matched_return_drafts'`.

- [ ] **Step 3: Implement the command**

Create `backend/apps/inventory/management/commands/post_matched_return_drafts.py`:

```python
"""Backfill 1 lần: ghi sổ các phiếu trả NHÁP đã khớp đơn (delivery_returned,
có dòng) — dọn tồn nháp cũ sau khi bật auto-post ở luồng quét.

Guard double-credit / đơn đã hoàn kho → skip + log, không chặn phiếu khác.
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnStatus, ReturnType


class Command(BaseCommand):
    help = "Ghi sổ hàng loạt phiếu trả nháp đã khớp đơn (delivery_returned, có dòng)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không ghi sổ")
        parser.add_argument(
            "--limit", type=int, default=0, help="Giới hạn số phiếu xử lý (0 = không giới hạn)"
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        limit = int(options.get("limit") or 0)

        qs = (
            ReturnNote.objects.filter(
                status=ReturnStatus.DRAFT,
                source_order__isnull=False,
                return_type=ReturnType.DELIVERY_RETURNED,
                lines__isnull=False,
            )
            .distinct()
            .order_by("id")
        )
        if limit > 0:
            qs = qs[:limit]

        candidate_ids = list(qs.values_list("id", flat=True))
        summary = {
            "dry_run": dry_run,
            "candidates": len(candidate_ids),
            "posted": 0,
            "skipped": 0,
            "skips": [],
        }

        for return_id in candidate_ids:
            if dry_run:
                code = ReturnNote.objects.values_list("code", flat=True).get(id=return_id)
                self.stdout.write(f"DRY-RUN sẽ ghi sổ: {code} (id={return_id})")
                continue
            try:
                rn = ReturnNoteService.post_return(return_note_id=return_id)
                summary["posted"] += 1
                self.stdout.write(self.style.SUCCESS(f"Đã ghi sổ {rn.code} (id={return_id})"))
            except ValueError as exc:
                summary["skipped"] += 1
                summary["skips"].append({"id": return_id, "error": str(exc)})
                self.stdout.write(self.style.WARNING(f"Bỏ qua id={return_id}: {exc}"))

        self.stdout.write(self.style.SUCCESS("post_matched_return_drafts done."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
```

- [ ] **Step 4: Run tests to verify they pass**

Lệnh như Step 2 (đổi `-e POSTGRES_DB=desi_bf2`).
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git add backend/apps/inventory/management/commands/post_matched_return_drafts.py \
        backend/apps/inventory/tests/test_post_matched_return_drafts_command.py
git commit -m "feat: add backfill command to post matched return drafts"
```

**Vận hành (prod, sau khi merge):** chạy `--dry-run` trước để xem số ứng viên, rồi chạy thật theo mẻ `--limit` nếu số lượng lớn. Lệnh chạy trong container prod tương ứng (không mount test image); phối hợp với người dùng khi thực thi trên prod.

---

## Final verification (sau tất cả task)

- [ ] **BE — chạy full return test suite (DB sạch):**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_final1 \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend:/app" -w /app \
  desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests -q'
```

Expected: tất cả pass (đối chiếu baseline known-failing nếu có fail lạ).

- [ ] **FE — typecheck + test:**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/frontend
npm run typecheck && npm run test -- inventory/scan
```

Expected: typecheck sạch; test scan pass.

- [ ] **Ruff (host):**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return/backend
uv run --no-project --with ruff ruff check \
  apps/inventory/application/services/return_note_service.py \
  apps/inventory/transport/views/return_views.py \
  apps/inventory/transport/serializers/return_serializers.py \
  apps/inventory/management/commands/post_matched_return_drafts.py \
  apps/inventory/tests/test_create_and_post_return.py \
  apps/inventory/tests/test_return_auto_post.py \
  apps/inventory/tests/test_post_matched_return_drafts_command.py
```

Expected: no issues.

- [ ] **Push + tạo MR** (sau khi kiểm tra conflict với master mới nhất):

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-auto-post-return
git fetch origin master && git merge --no-edit origin/master   # resolve nếu có conflict
git push -u origin feat/auto-post-return-on-scan
```

Gửi link MR sau khi push.
```
```

## Manual QA checklist (người dùng nghiệm thu)

- [ ] Trang Phiếu kho → "Quét trả hàng (hàng loạt)": quét 1 mã **khớp đơn** → Xác nhận → dòng hiện "Đã ghi sổ TH…"; kiểm tra phiếu ở trạng thái **Đã ghi sổ** (không còn nháp), tồn kho cộng đúng, đơn nguồn chuyển **Đã chuyển hoàn**, và KiotViet cập nhật trạng thái giao.
- [ ] Quét 1 mã **chưa khớp** → vẫn vào "chờ khớp" như cũ.
- [ ] Quét 1 mã khớp đơn nhưng đơn đã hoàn kho/huỷ (nếu dựng được) → dòng báo **lỗi đỏ**, không tạo phiếu.
- [ ] Tạo phiếu trả **bằng tay** (CreateReturnDialog) → vẫn ra **nháp**, ghi sổ tay như trước.
