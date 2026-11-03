# KOC Order Payment By Type — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đơn KOC loại "không trả lại" tự đánh đã thanh toán đủ; loại "có trả lại" đẩy KiotViet nhưng không thu COD và để công nợ; mọi đơn KOC ghi `order_source="koc"` và khách mới nhận mã prefix `KOC`.

**Architecture:** Backend là nguồn chân lý. (1) Khi tạo đơn KOC `no_return`, service ghi đè `customer_prepaid = total_after_discount`. (2) Khi đẩy KiotViet đơn KOC `has_return`, ép `isCod=false`, `codAmount=0`, `totalPayment=0`. (3) Frontend chỉ phản ánh UI: tự điền prepaid khi chọn `no_return` trong chế độ KOC, qua một helper thuần dễ test.

**Tech Stack:** Django (Python) backend, React + TypeScript + Vitest frontend.

---

## Bối cảnh test (đọc trước khi chạy)

Backend pytest hỏng (BOM trong `pytest.ini`). Dùng Django runner với Postgres + Redis tạm:

```bash
# CHỈ bind loopback (audit 2026-09-23 N4: `-p 5432:5432` publish 0.0.0.0 — trên host
# prod là mở DB/Redis test ra Internet). Redis bắt buộc --requirepass.
docker run -d --name desi-test-pg -e POSTGRES_DB=desi_order -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -p 127.0.0.1:55432:5432 public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis -p 127.0.0.1:56379:6379 public.ecr.aws/docker/library/redis:7-alpine redis-server --requirepass 'Desi@2026'
```

Lệnh chạy test backend (interpreter là venv ở REPO ROOT):

```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=56379 REDIS_PASSWORD=Desi@2026 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test <dotted.path> -v2
```

Frontend test: `cd frontend && npx vitest run <file>`. `KOC_CHANNEL_ID` mặc định = 15; test tạo `Channel` với `id=settings.KOC_CHANNEL_ID`.

## File Structure

- **Modify** `backend/apps/orders/application/services/order_service_commands.py` — thêm override `customer_prepaid` cho KOC `no_return` trong `create_order_manual`.
- **Modify** `backend/apps/orders/tests/test_order_create_api.py` — test create KOC `no_return`.
- **Modify** `backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py` — ép no-COD + `totalPayment=0` cho KOC `has_return`; thêm `from django.conf import settings`.
- **Modify** `backend/apps/kiotviet/tests/test_access_token.py` — test push payload KOC `has_return`.
- **Create** `frontend/src/features/orders/koc-order-form.ts` — helper thuần `kocPrepaidOnOrderTypeChange`.
- **Create** `frontend/src/features/orders/koc-order-form.test.ts` — test helper.
- **Modify** `frontend/src/features/orders/OrderFormDialog.tsx` — thêm prop `kocMode`, gọi helper trong `onValueChange` của ô loại đơn.
- **Modify** `frontend/src/pages/OrdersPage.tsx` — thêm prop `isKoc`, truyền `kocMode` xuống dialog.
- **Modify** `frontend/src/app/routed-pages.tsx` — truyền `isKoc` từ `KocOrdersRoute`.
- **Modify** `backend/apps/orders/models.py` — thêm `KOC` vào `OrderSource` (Task 5).
- **Create** `backend/apps/orders/migrations/00XX_alter_order_order_source.py` — migration đổi choices (Task 5).
- **Modify** `backend/apps/customers/models.py` — prefix mã KH cấu hình được (Task 6).
- **Modify** `backend/apps/customers/services.py` — `get_or_create_by_phone(..., code_prefix)` (Task 6).
- **Modify** `frontend/src/features/orders/order-types.ts` & `order-source.ts` — type/label/badge cho `koc` (Task 7).

> **Lưu ý:** `create_order_manual` được sửa ở Task 1, 5, 6. Khi chạy subagent-driven, thực thi theo thứ tự để tránh xung đột trên cùng hàm.

---

## Task 1: Backend — KOC `no_return` tự đánh đã thanh toán

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (≈ dòng 445-448 và 509)
- Test: `backend/apps/orders/tests/test_order_create_api.py`

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối class `OrderCreateApiTests` trong `backend/apps/orders/tests/test_order_create_api.py`:

```python
    def _create_koc_channel(self):
        from django.conf import settings
        from apps.channels_core.models import UserChannel

        koc = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hang mau koc"
        )
        UserChannel.objects.create(user=self.admin, channel=koc, is_active=True)
        return koc

    def test_koc_no_return_order_is_marked_fully_paid(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC No Return",
                "phone": "0909 000 111",
                "order_type": "no_return",
                "total_amount": "500000",
                "discount_amount": "0",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="KOC No Return").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.customer_prepaid, order.total_after_discount)
        self.assertEqual(int(order.customer_prepaid), 500000)

    def test_koc_has_return_order_keeps_prepaid_untouched(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC Has Return",
                "phone": "0909 000 222",
                "order_type": "has_return",
                "total_amount": "500000",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="KOC Has Return").first()
        self.assertEqual(int(order.customer_prepaid), 0)

    def test_non_koc_no_return_order_is_not_overridden(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal Channel No Return",
                "phone": "0909 000 333",
                "order_type": "no_return",
                "total_amount": "500000",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal Channel No Return").first()
        self.assertEqual(int(order.customer_prepaid), 0)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api.OrderCreateApiTests.test_koc_no_return_order_is_marked_fully_paid -v2
```
Expected: FAIL — `customer_prepaid` = 0 ≠ 500000.

- [ ] **Step 3: Cài đặt override**

Trong `backend/apps/orders/application/services/order_service_commands.py`, ngay sau khối tính `payload_total_after_discount` (sau dòng 448, trước `kiotviet_channel = payload.get(...)`), thêm:

```python
        # Đơn KOC loại "không trả lại" coi như đã thanh toán đủ (prepaid = total).
        # `settings` có sẵn qua star-import từ order_service_shared.
        if (
            channel.id == settings.KOC_CHANNEL_ID
            and payload.get("order_type") == "no_return"
        ):
            payload_customer_prepaid = payload_total_after_discount
        else:
            payload_customer_prepaid = payload.get("customer_prepaid", 0)
```

Rồi sửa dòng tạo Order (dòng 509) từ:

```python
            customer_prepaid=payload.get("customer_prepaid", 0),
```
thành:
```python
            customer_prepaid=payload_customer_prepaid,
```

- [ ] **Step 4: Chạy 3 test Task 1, xác nhận PASS**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api -v2
```
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_order_create_api.py
git commit -m "Mark KOC no_return orders as fully paid on create"
```

---

## Task 2: Backend — KOC `has_return` không thu COD, để công nợ

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py` (thêm import settings; ≈ dòng 462-476 và 557-562)
- Test: `backend/apps/kiotviet/tests/test_access_token.py`

- [ ] **Step 1: Viết test thất bại**

Thêm method vào class `KiotVietPushOrderCustomerFlowTests` trong `backend/apps/kiotviet/tests/test_access_token.py` (class này đã import `Channel`, `Product`, `OrderItem`, `patch`, `push_order_to_kiotviet`):

```python
    @patch("apps.kiotviet.services.create_order")
    @patch("apps.kiotviet.services.get_or_create_customer")
    def test_push_koc_has_return_no_cod_and_debt(self, mock_get_or_create_customer, mock_create_order):
        from django.conf import settings

        koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hang mau koc"
        )
        self.order.channel = koc_channel
        self.order.order_type = "has_return"
        self.order.total_after_discount = 100000
        self.order.customer_prepaid = 0
        self.order.save(update_fields=["channel", "order_type", "total_after_discount", "customer_prepaid", "updated_at"])

        product = Product.objects.create(name="KOC Sample", sku="SKU-KOC-01", price=100000)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=100000,
            line_total=100000,
        )

        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 999, "code": "OD-KOC-01"}

        push_order_to_kiotviet(self.order, self.kiotviet_channel)

        order_payload = mock_create_order.call_args.args[0]
        self.assertFalse(order_payload["orderDelivery"]["isCod"])
        self.assertEqual(order_payload["orderDelivery"]["codAmount"], 0)
        self.assertEqual(order_payload["orderDelivery"]["receiveMoney"], 0)
        self.assertEqual(order_payload["totalPayment"], 0)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.kiotviet.tests.test_access_token.KiotVietPushOrderCustomerFlowTests.test_push_koc_has_return_no_cod_and_debt -v2
```
Expected: FAIL — `isCod=True`, `codAmount=100000`, `totalPayment=100000` (hành vi cũ).

- [ ] **Step 3: Thêm import settings**

Đầu file `backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py`, thêm (nếu chưa có):

```python
from django.conf import settings
```

- [ ] **Step 4: Ép no-COD ở block order_delivery**

Trong `push_order_to_kiotviet`, ngay sau dòng tính `cod_amount` (dòng 464, trước khi dựng dict `order_delivery`), thêm:

```python
    # Đơn KOC loại "có trả lại": đẩy đơn nhưng không thu COD, để công nợ.
    is_koc_has_return = (
        getattr(order, "channel_id", None) == settings.KOC_CHANNEL_ID
        and str(getattr(order, "order_type", "") or "") == "has_return"
    )
    if is_koc_has_return:
        is_cod = False
        cod_amount = 0
```

(`order_delivery` đã dùng `is_cod`/`cod_amount` nên không cần sửa dict.)

- [ ] **Step 5: Sửa nhánh totalPayment cho has_return**

Thay khối dòng 557-562 hiện tại:

```python
    if str(getattr(order, "order_type", "") or "") == "has_return":
        order_data["totalPayment"] = total_after_discount_amount
        order_data["method"] = payment_method
    elif customer_prepaid_amount > 0:
        order_data["totalPayment"] = customer_prepaid_amount
        order_data["method"] = payment_method
```

thành:

```python
    if is_koc_has_return:
        # KOC "có trả lại": để công nợ (không đánh đã trả).
        order_data["totalPayment"] = 0
        order_data["method"] = payment_method
    elif str(getattr(order, "order_type", "") or "") == "has_return":
        # has_return ngoài KOC: giữ hành vi cũ — đánh đã trả đủ.
        order_data["totalPayment"] = total_after_discount_amount
        order_data["method"] = payment_method
    elif customer_prepaid_amount > 0:
        order_data["totalPayment"] = customer_prepaid_amount
        order_data["method"] = payment_method
```

- [ ] **Step 6: Chạy test mới + regression, xác nhận PASS**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.kiotviet.tests.test_access_token -v2
```
Expected: test mới PASS; `test_push_order_payload_price_mapping_new_rules` (non-KOC) vẫn PASS với `codAmount=123456`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py backend/apps/kiotviet/tests/test_access_token.py
git commit -m "Skip COD and keep debt for KOC has_return orders on KiotViet push"
```

---

## Task 3: Frontend — helper thuần tự điền prepaid khi chọn `no_return`

**Files:**
- Create: `frontend/src/features/orders/koc-order-form.ts`
- Test: `frontend/src/features/orders/koc-order-form.test.ts`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/orders/koc-order-form.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { kocPrepaidOnOrderTypeChange } from "@/features/orders/koc-order-form";

describe("kocPrepaidOnOrderTypeChange", () => {
  it("returns total_after_discount when KOC mode and no_return", () => {
    expect(kocPrepaidOnOrderTypeChange("no_return", true, "500000", "600000")).toBe("500000");
  });

  it("falls back to total_amount when total_after_discount empty", () => {
    expect(kocPrepaidOnOrderTypeChange("no_return", true, "", "600000")).toBe("600000");
  });

  it("returns null when not KOC mode", () => {
    expect(kocPrepaidOnOrderTypeChange("no_return", false, "500000", "600000")).toBeNull();
  });

  it("returns null for has_return and normal", () => {
    expect(kocPrepaidOnOrderTypeChange("has_return", true, "500000", "600000")).toBeNull();
    expect(kocPrepaidOnOrderTypeChange("normal", true, "500000", "600000")).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/orders/koc-order-form.test.ts`
Expected: FAIL — module chưa tồn tại.

- [ ] **Step 3: Cài đặt helper**

Tạo `frontend/src/features/orders/koc-order-form.ts`:

```ts
/**
 * Khi đổi loại đơn trong chế độ KOC, đơn "không trả lại" (no_return) được coi như
 * đã thanh toán đủ → trả về giá trị customer_prepaid cần set (= total_after_discount,
 * fallback total_amount). Trả về null nếu không cần đổi prepaid.
 */
export function kocPrepaidOnOrderTypeChange(
  orderType: string,
  kocMode: boolean,
  totalAfterDiscount: string | number | undefined,
  totalAmount: string | number | undefined,
): string | null {
  if (!kocMode || orderType !== "no_return") {
    return null;
  }
  const value = totalAfterDiscount || totalAmount || "0";
  return String(value);
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/orders/koc-order-form.test.ts`
Expected: 4 test PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/orders/koc-order-form.ts frontend/src/features/orders/koc-order-form.test.ts
git commit -m "Add KOC prepaid helper for order type change"
```

---

## Task 4: Frontend — nối `kocMode` vào form và áp dụng helper

**Files:**
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx` (props ≈ dòng 81-118; ô loại đơn ≈ dòng 1014-1025)
- Modify: `frontend/src/pages/OrdersPage.tsx` (props ≈ dòng 224-225; render dialog ≈ dòng 1939)
- Modify: `frontend/src/app/routed-pages.tsx` (`KocOrdersRoute` ≈ dòng 224-234)

- [ ] **Step 1: Thêm prop `kocMode` vào OrderFormDialog**

Trong `frontend/src/features/orders/OrderFormDialog.tsx`, thêm vào `OrderFormDialogProps` (sau `submitting: boolean;` dòng 91):

```ts
  /** Đang ở trang đơn KOC — bật auto-fill prepaid khi chọn "không trả lại". */
  kocMode?: boolean;
```

Thêm vào destructure tham số của `export function OrderFormDialog({ ... })` (≈ dòng 375): thêm `kocMode = false,` vào danh sách.

Thêm import ở đầu file (cạnh các import `@/features/orders/...`):

```ts
import { kocPrepaidOnOrderTypeChange } from "@/features/orders/koc-order-form";
```

- [ ] **Step 2: Áp dụng helper trong onValueChange của ô loại đơn**

Thay handler `onValueChange` của Select loại đơn (dòng 1016):

```tsx
                onValueChange={(value) => setForm((prev) => ({ ...prev, order_type: value || "normal" }))}
```

thành:

```tsx
                onValueChange={(value) =>
                  setForm((prev) => {
                    const nextType = value || "normal";
                    const prepaid = kocPrepaidOnOrderTypeChange(
                      nextType,
                      kocMode,
                      prev.total_after_discount,
                      prev.total_amount,
                    );
                    return prepaid === null
                      ? { ...prev, order_type: nextType }
                      : { ...prev, order_type: nextType, customer_prepaid: prepaid };
                  })
                }
```

- [ ] **Step 3: Truyền `kocMode` từ OrdersPage**

Trong `frontend/src/pages/OrdersPage.tsx`:

Thêm vào props type (sau `hideChannelFilter?: boolean;` dòng 225):
```ts
  /** Trang đơn KOC — bật auto-fill thanh toán cho đơn "không trả lại". */
  isKoc?: boolean;
```

Thêm vào destructure props của component (cạnh `hideChannelFilter = false,` dòng 544):
```ts
  isKoc = false,
```

Truyền xuống dialog (trong block `<OrderFormDialog ... />` dòng 1939, thêm một dòng prop):
```tsx
        kocMode={isKoc}
```

- [ ] **Step 4: Truyền `isKoc` từ KocOrdersRoute**

Trong `frontend/src/app/routed-pages.tsx`, ở `KocOrdersRoute`, thêm prop `isKoc` cạnh `hideChannelFilter` (dòng 227):

```tsx
          hideChannelFilter
          isKoc
```

- [ ] **Step 5: Build/typecheck frontend, xác nhận không lỗi type**

Run: `cd frontend && npx tsc --noEmit`
Expected: không có lỗi liên quan tới `kocMode`/`isKoc`.

- [ ] **Step 6: Chạy lại test helper + lint nhanh**

Run: `cd frontend && npx vitest run src/features/orders/koc-order-form.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/orders/OrderFormDialog.tsx frontend/src/pages/OrdersPage.tsx frontend/src/app/routed-pages.tsx
git commit -m "Auto-fill prepaid for KOC no_return orders in order form"
```

---

## Task 5: Backend — đơn KOC ghi `order_source = "koc"`

**Files:**
- Modify: `backend/apps/orders/models.py` (`OrderSource`, ≈ dòng 38-43)
- Create: migration `backend/apps/orders/migrations/00XX_alter_order_order_source.py` (auto qua makemigrations)
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (`create_order_manual`, khối `order_source=` ≈ dòng 503-507)
- Test: `backend/apps/orders/tests/test_order_create_api.py`

- [ ] **Step 1: Viết test thất bại**

Thêm vào class `OrderCreateApiTests` (dùng lại helper `_create_koc_channel` từ Task 1):

```python
    def test_koc_order_source_is_koc(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC Source",
                "phone": "0909 000 444",
                "order_source": "zalo",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="KOC Source").first()
        self.assertEqual(order.order_source, "koc")

    def test_non_koc_order_source_is_not_forced(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal Source",
                "phone": "0909 000 555",
                "order_source": "zalo",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal Source").first()
        self.assertEqual(order.order_source, "zalo")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api.OrderCreateApiTests.test_koc_order_source_is_koc -v2
```
Expected: FAIL — `order_source` = "zalo" hoặc None, không phải "koc" (giá trị "koc" cũng chưa hợp lệ trong enum).

- [ ] **Step 3: Thêm giá trị `koc` vào enum**

Trong `backend/apps/orders/models.py`, thêm dòng vào `OrderSource` (sau `HOTLINE`, dòng 43):

```python
    KOC = "koc", "KOC"
```

- [ ] **Step 4: Tạo migration**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py makemigrations orders
```
Expected: sinh file `00XX_alter_order_order_source.py` (AlterField order_source).

- [ ] **Step 5: Ép order_source cho đơn KOC**

Trong `create_order_manual`, sửa khối `order_source=(...)` (dòng 503-507) thành:

```python
            order_source=(
                "koc"
                if channel.id == settings.KOC_CHANNEL_ID
                else (
                    payload.get("order_source")
                    or ("landing" if payload.get("show_orders") is False else None)
                )
            ),
```

- [ ] **Step 6: Chạy test, xác nhận PASS**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api -v2
```
Expected: tất cả PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_order_create_api.py
git commit -m "Set order_source to koc for KOC channel orders"
```

---

## Task 6: Backend — khách mới ở luồng KOC nhận mã prefix "KOC"

**Files:**
- Modify: `backend/apps/customers/models.py` (`Customer.save`, dòng 54-65)
- Modify: `backend/apps/customers/services.py` (`get_or_create_by_phone`, dòng 75-113)
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (`create_order_manual`, lời gọi dòng 414-417)
- Test: `backend/apps/orders/tests/test_order_create_api.py`

- [ ] **Step 1: Viết test thất bại**

Thêm vào class `OrderCreateApiTests`:

```python
    def test_koc_new_customer_uses_koc_prefix(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC New Cust",
                "phone": "0911 222 333",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="KOC New Cust").first()
        self.assertTrue(order.customer.code.startswith("KOC"), order.customer.code)

    def test_non_koc_new_customer_uses_kh_prefix(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal New Cust",
                "phone": "0911 222 444",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal New Cust").first()
        self.assertTrue(order.customer.code.startswith("KH"), order.customer.code)

    def test_koc_existing_customer_keeps_old_code(self):
        from apps.customers.models import Customer

        existing = Customer.objects.create(
            full_name="Old Cust", phone_raw="0911555666", phone_normalized="0911555666"
        )
        old_code = existing.code  # KH<id>
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "Old Cust",
                "phone": "0911 555 666",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        existing.refresh_from_db()
        self.assertEqual(existing.code, old_code)
        self.assertTrue(old_code.startswith("KH"))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api.OrderCreateApiTests.test_koc_new_customer_uses_koc_prefix -v2
```
Expected: FAIL — code bắt đầu bằng "KH".

- [ ] **Step 3: Cho phép prefix cấu hình trong `Customer.save`**

Trong `backend/apps/customers/models.py`, thay khối cuối `save` (dòng 61-65):

```python
        if is_creating and self.code.startswith("TMP") and self.pk:
            final_code = f"KH{self.pk}"
            if self.code != final_code:
                Customer.objects.filter(pk=self.pk).update(code=final_code)
                self.code = final_code
```

thành:

```python
        if is_creating and self.code.startswith("TMP") and self.pk:
            prefix = getattr(self, "_code_prefix", "KH") or "KH"
            final_code = f"{prefix}{self.pk}"
            if self.code != final_code:
                Customer.objects.filter(pk=self.pk).update(code=final_code)
                self.code = final_code
```

- [ ] **Step 4: Thêm tham số `code_prefix` vào `get_or_create_by_phone`**

Trong `backend/apps/customers/services.py`, đổi chữ ký (dòng 76):

```python
    def get_or_create_by_phone(phone: str, full_name: str = "", code_prefix: str = "KH") -> Customer:
```

và thay khối tạo mới (dòng 108-113):

```python
        # Tạo customer mới; code sẽ được model chuẩn hóa thành KH<id>.
        return Customer.objects.create(
            full_name=full_name,
            phone_raw=phone,
            phone_normalized=phone_normalized,
        )
```

thành:

```python
        # Tạo customer mới; code chuẩn hóa thành {prefix}<id> (mặc định KH).
        new_customer = Customer(
            full_name=full_name,
            phone_raw=phone,
            phone_normalized=phone_normalized,
        )
        new_customer._code_prefix = code_prefix
        new_customer.save()
        return new_customer
```

- [ ] **Step 5: Truyền prefix KOC từ `create_order_manual`**

Trong `backend/apps/orders/application/services/order_service_commands.py`, thay lời gọi (dòng 414-417):

```python
        customer = CustomerService.get_or_create_by_phone(
            phone=phone_raw,
            full_name=name
        )
```

thành:

```python
        customer = CustomerService.get_or_create_by_phone(
            phone=phone_raw,
            full_name=name,
            code_prefix="KOC" if channel.id == settings.KOC_CHANNEL_ID else "KH",
        )
```

- [ ] **Step 6: Chạy test create + customers, xác nhận PASS**

Run:
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 /Users/desi/Desktop/workspace/desi-order/.venv/bin/python manage.py test apps.orders.tests.test_order_create_api apps.customers -v2
```
Expected: test mới PASS; test customers cũ không hồi quy (lưu ý: có 1 test aggregate fail sẵn trên master — bỏ qua, xem memory).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/customers/models.py backend/apps/customers/services.py backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_order_create_api.py
git commit -m "Use KOC prefix for new customers created via KOC orders"
```

---

## Task 7: Frontend — hiển thị badge nguồn đơn "KOC"

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (type `OrderSource`, dòng 2)
- Modify: `frontend/src/features/orders/order-source.ts` (labels + badge class)

- [ ] **Step 1: Thêm "koc" vào type `OrderSource`**

Trong `frontend/src/features/orders/order-types.ts`, dòng 2:

```ts
export type OrderSource = "landing" | "kol" | "zalo" | "messenger" | "hotline";
```
thành:
```ts
export type OrderSource = "landing" | "kol" | "zalo" | "messenger" | "hotline" | "koc";
```

- [ ] **Step 2: Thêm label + badge class (KHÔNG thêm vào ORDER_SOURCES)**

Trong `frontend/src/features/orders/order-source.ts`, thêm key `koc` vào `ORDER_SOURCE_LABELS` (sau `hotline`, dòng 20):

```ts
  koc: "KOC",
```

và vào map trong `orderSourceBadgeClass` (sau `hotline`, dòng 36):

```ts
    koc: "bg-amber-100 text-amber-700 border-amber-300",
```

Giữ nguyên `ORDER_SOURCES` (không thêm "koc") để koc không xuất hiện trong dropdown chọn nguồn.

- [ ] **Step 3: Typecheck frontend, xác nhận không lỗi**

Run: `cd frontend && npx tsc --noEmit`
Expected: không lỗi. (TS yêu cầu `Record<OrderSource,...>` đủ key — đã thêm `koc` ở cả 2 map.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/orders/order-types.ts frontend/src/features/orders/order-source.ts
git commit -m "Show KOC badge for koc order source"
```

---

## Hoàn tất

- [ ] **Đẩy branch & tạo MR**

```bash
git push -u origin koc-order-payment-by-type-20260602
```
Gửi link MR cho người dùng (theo CLAUDE.md).

## Ghi chú phạm vi
- Chỉ áp dụng cho kênh KOC (`KOC_CHANNEL_ID`); kênh khác giữ nguyên.
- Phần `no_return → prepaid` chỉ chạy lúc **tạo** đơn. Phần COD/công nợ của `has_return` đọc `order_type` lúc push nên đúng kể cả khi đổi loại qua edit.
- Đường đẩy `POST /invoices` (`use_invoice_api=True`) không gửi COD nên không thuộc phạm vi sửa; nếu sau này KOC dùng đường này, cần xử lý riêng `totalPayment` cho `has_return`.
