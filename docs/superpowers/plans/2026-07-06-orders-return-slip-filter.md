# Bộ lọc "Phiếu trả hàng" (has_return_slip) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm bộ lọc Có/Không cho field "Phiếu trả hàng" (`OrderRecall.return_slip_code`) trên cả 3 trang danh sách đơn (Đơn hàng, Landing, KOC), mirror 100% pattern filter `has_internal_note`/`has_address`.

**Architecture:** Backend thêm param `has_return_slip` (`"yes"`/`"no"`/rỗng) vào `OrderRepository._apply_list_filters` bằng Exists semi-/anti-join sang `order_recalls` (probe unique index `order_id` của OneToOne), và parse param ở cả 4 chỗ build filter dict (parity). Frontend thêm state `hasReturnSlip` đi đúng chuỗi `hasAddress` (analog mới nhất): storage → filters hook → data hook (`buildBody` + client-side keep-row) → OrdersPage → OrdersFilterPanel, dùng chung cho cả 3 trang.

**Tech Stack:** Django/DRF + Django ORM Exists/OuterRef; React + TypeScript, vitest; pytest chạy qua Docker image `desi-order-web-test`.

**Spec:** `docs/superpowers/specs/2026-07-06-orders-return-slip-filter-design.md`

## Global Constraints

- Ngữ nghĩa: `yes` = tồn tại `order_recalls` với `return_slip_code != ""`; `no` = phần bù (không có recall HOẶC recall có slip rỗng); rỗng = không lọc.
- Tên param BE: `has_return_slip`. Tên state FE: `hasReturnSlip` / setter `setHasReturnSlip`.
- i18n keys: `orders.filters.hasReturnSlip`, `...All`, `...Yes`, `...No` (vi: "Phiếu trả hàng" / "Tất cả" / "Có phiếu trả" / "Chưa có phiếu trả"; en: "Return slip" / "All" / "Has return slip" / "No return slip").
- KHÔNG index mới, KHÔNG regen `schema.d.ts`, KHÔNG đụng trang Vận đơn.
- Commit message tiếng Anh, ngắn, KHÔNG Co-Authored-By.
- Test BE chạy qua Docker (xem lệnh ở Task 1 Step 2); KHÔNG dùng `cache.clear()` trong test (FLUSHDB Redis chung).
- Baseline fail sẵn trên master (return-rate BE; OrderFormDialog + 2 mobile-viewport FE) — không phải regression.
- Làm việc trong worktree `.claude/worktrees/feat-orders-return-slip-filter`, branch `feat/orders-return-slip-filter` — KHÔNG tự tạo branch khác.

---

### Task 1: Backend — filter `has_return_slip` + parse ở 4 view + tests

**Files:**
- Create: `backend/apps/orders/tests/test_return_slip_filter.py`
- Modify: `backend/apps/orders/repositories.py` (import ~line 20-29; block filter sau `has_internal_note` ~line 588)
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py` (`build_orders_filters`, sau dòng `"has_internal_note"` ~118)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (`list_koc_orders` ~143, `list_landing_orders` ~244)
- Modify: `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py` (~91)

**Interfaces:**
- Produces: list endpoints `/api/orders`, `/api/orders/koc`, `/api/orders/landing`, `/api/orders/sheet3` chấp nhận body field `has_return_slip: "yes" | "no" | ""` — Task 3 (FE `buildBody`) gửi field này.

- [ ] **Step 1: Viết test fail** — tạo `backend/apps/orders/tests/test_return_slip_filter.py` (mẫu theo `test_ingest_has_address.py::HasAddressListFilterApiTests`):

```python
"""Bộ lọc has_return_slip (yes/no/rỗng) — Exists sang order_recalls.return_slip_code.

yes = có bản ghi recall với return_slip_code khác rỗng; no = phần bù (không có
recall HOẶC recall slip rỗng). Parse ở cả 4 list endpoint dùng chung OrdersFilterPanel.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderRecall
from apps.users.test_helpers import make_admin


class ReturnSlipFilterApiTests(TestCase):
    """3 case dữ liệu: recall có phiếu / recall phiếu rỗng / không có recall."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-return-slip-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="slip-ch", name="Slip Ch", owner="Slip Ch")
        self.customer = Customer.objects.create(
            full_name="Khach Phieu Tra",
            phone_raw="0909000020",
            phone_normalized="0909000020",
        )
        # Nguồn "landing" để hiện trên /api/orders và /api/orders/landing.
        self.order_with_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Co Phieu Tra",
            phone="0909000020",
            order_source="landing",
        )
        OrderRecall.objects.create(order=self.order_with_slip, return_slip_code="PT-001")
        self.order_empty_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Recall Phieu Rong",
            phone="0909000021",
            order_source="landing",
        )
        OrderRecall.objects.create(order=self.order_empty_slip, return_slip_code="")
        self.order_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Khong Recall",
            phone="0909000022",
            order_source="landing",
        )
        # Đơn KOC riêng cho endpoint /api/orders/koc (landing loại trừ koc).
        self.koc_with_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Co Phieu Tra",
            phone="0909000023",
            order_source="koc",
        )
        OrderRecall.objects.create(order=self.koc_with_slip, return_slip_code="PT-KOC-1")
        self.koc_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Khong Recall",
            phone="0909000024",
            order_source="koc",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_orders_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders", {"has_return_slip": "yes"}),
            {self.order_with_slip.id, self.koc_with_slip.id},
        )
        self.assertEqual(
            self._ids("/api/orders", {"has_return_slip": "no"}),
            {self.order_empty_slip.id, self.order_no_recall.id, self.koc_no_recall.id},
        )
        all_ids = self._ids("/api/orders", {})
        self.assertIn(self.order_with_slip.id, all_ids)
        self.assertIn(self.order_empty_slip.id, all_ids)
        self.assertIn(self.order_no_recall.id, all_ids)

    def test_landing_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_return_slip": "yes"}),
            {self.order_with_slip.id},
        )
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_return_slip": "no"}),
            {self.order_empty_slip.id, self.order_no_recall.id},
        )

    def test_koc_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_return_slip": "yes"}), {self.koc_with_slip.id}
        )
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_return_slip": "no"}), {self.koc_no_recall.id}
        )
```

Lưu ý khi chạy: nếu `test_orders_list_filters_by_has_return_slip` fail vì `/api/orders` không trả đơn koc (list chính có thể lọc show_orders/nguồn), sửa assert của endpoint đó về chỉ 3 đơn landing (bỏ `koc_*` khỏi expected của `/api/orders`) — phần koc đã được cover bởi test `/api/orders/koc`. KHÔNG sửa logic filter vì chuyện này.

- [ ] **Step 2: Chạy test, xác nhận fail đúng lý do** (filter chưa tồn tại → `has_return_slip: "yes"` trả cả 5 đơn). Từ repo gốc `c:\Users\MYPC\Desktop\workspace\desi-order` KHÔNG được — chạy từ worktree root, Git Bash. Kiểm tra stack đang chạy bằng `docker ps` trước (xem memory running-backend-tests); với stack `docker-compose.yml` (network `desi-order_default`):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/orders/tests/test_return_slip_filter.py -q
```

Expected: FAIL — assert set mismatch (chưa lọc).

- [ ] **Step 3: Implement.** 3 sửa đổi:

(a) `backend/apps/orders/repositories.py` — thêm `OrderRecall` vào import models (dòng 20-29):

```python
from .models import (
    Order,
    OrderAssessment,
    OrderItem,
    OrderJourneyEvent,
    OrderKiotVietJob,
    OrderKiotVietJobStatus,
    OrderNeed,
    OrderRecall,
    Product,
)
```

(b) `repositories.py` — chèn block NGAY SAU block `has_internal_note` (sau dòng 588 `queryset = queryset.filter(~note_exists)`, trước `delivery_business_status = ...`):

```python
        # "Phiếu trả hàng" nằm trên OrderRecall (relation `recall`, 1-1) — cùng
        # pattern Exists như has_internal_note: drive từ tập đơn đã lọc, probe
        # order_recalls qua unique index order_id.
        has_return_slip = str(filters.get("has_return_slip") or "").strip()
        if has_return_slip in ("yes", "no"):
            slip_exists = Exists(
                OrderRecall.objects.filter(order_id=OuterRef("pk")).exclude(return_slip_code="")
            )
            if has_return_slip == "yes":
                queryset = queryset.filter(slip_exists)
            else:
                queryset = queryset.filter(~slip_exists)
```

(c) Parse param ở 4 view — chèn 1 dòng vào mỗi filter dict:

- `_view_helpers.py::build_orders_filters` — sau `"has_internal_note": source.get("has_internal_note", ""),`:
  ```python
        "has_return_slip": source.get("has_return_slip", ""),
  ```
- `order_list_view.py::list_koc_orders` (~143) — sau dòng `"has_internal_note"`: cùng dòng trên.
- `order_list_view.py::list_landing_orders` (~244) — sau dòng `"has_internal_note"`: cùng dòng trên.
- `sheet3_view.py` (~91) — sau dòng `"has_address": source.get("has_address", ""),`:
  ```python
        # "Phiếu trả hàng" cũng hiển thị ở panel dùng chung — parse vì filter parity.
        "has_return_slip": source.get("has_return_slip", ""),
  ```

- [ ] **Step 4: Chạy lại test** (lệnh Step 2). Expected: PASS (3 tests).

- [ ] **Step 5: Ruff + commit:**

```bash
/c/Users/MYPC/.local/bin/uv run --no-project --with ruff ruff check \
  backend/apps/orders/repositories.py \
  backend/apps/orders/tests/test_return_slip_filter.py \
  backend/apps/orders/transport/views/order_views_impl/_view_helpers.py \
  backend/apps/orders/transport/views/order_views_impl/order_list_view.py \
  backend/apps/orders/transport/views/order_views_impl/sheet3_view.py
git add backend/apps/orders && git commit -m "feat(orders): add has_return_slip filter to list endpoints"
```

---

### Task 2: FE state — storage + filters hook + unit test

**Files:**
- Modify: `frontend/src/features/orders/order-storage.ts`
- Modify: `frontend/src/features/orders/use-orders-filters.ts`
- Test: `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`

**Interfaces:**
- Produces: `StoredOrderFilters.hasReturnSlip: string` (định nghĩa duy nhất ở `order-storage.ts`, re-export qua `use-orders-data.ts`); hook `useOrdersFilters` trả `hasReturnSlip` + `setHasReturnSlip`, `reset()` đưa về `""`. Task 3/4 dùng đúng các tên này.

- [ ] **Step 1: Viết test fail** — sửa `use-orders-filters.test.tsx`:

(a) `makeStored`: thêm `hasReturnSlip: "",` sau `hasAddress: "",` (dòng 28).

(b) Test seed (it thứ 1): thêm vào `makeStored({...})` overrides `hasReturnSlip: "yes",` và assert:
```ts
    expect(result.current.hasReturnSlip).toBe("yes");
```

(c) Test reset (it thứ 3): trong act đầu thêm `result.current.setHasReturnSlip("no");` và sau reset assert:
```ts
    expect(result.current.hasReturnSlip).toBe("");
```

- [ ] **Step 2: Chạy test, xác nhận fail** (type error / undefined):

```bash
cd frontend && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx
```

Expected: FAIL — `setHasReturnSlip is not a function` (hoặc TS error).

- [ ] **Step 3: Implement.**

(a) `order-storage.ts` — 5 chỗ, mỗi chỗ chèn ngay sau dòng `hasAddress` tương ứng:
- Type `StoredOrderFilters` (~107): `hasReturnSlip: string;`
- Default khi không có raw (~173) VÀ default trong catch (~249): `hasReturnSlip: "",`
- 2 nhánh parse (~202, ~227): `hasReturnSlip: String(parsed?.hasReturnSlip ?? "").trim(),`

(b) `use-orders-filters.ts` — chèn sau mỗi dòng `hasAddress`/`setHasAddress`:
- Type return (~38): `hasReturnSlip: string;`
- Type setter (~59): `setHasReturnSlip: React.Dispatch<React.SetStateAction<string>>;`
- State (~90): `const [hasReturnSlip, setHasReturnSlip] = useState<string>(storedFilters.hasReturnSlip);`
- `reset()` (~112): `setHasReturnSlip("");`
- Return value (~136): `hasReturnSlip,` — return setter (~157): `setHasReturnSlip,`

- [ ] **Step 4: Chạy lại test.** Expected: PASS (3 tests). Lưu ý `npm run typecheck` toàn dự án sẽ CHƯA pass ở task này (use-orders-data chưa có field — Task 3); chỉ cần vitest file này pass.

- [ ] **Step 5: Commit:**

```bash
git add frontend/src/features/orders/order-storage.ts frontend/src/features/orders/use-orders-filters.ts frontend/src/features/orders/__tests__/use-orders-filters.test.tsx
git commit -m "feat(orders): add hasReturnSlip filter state"
```

---

### Task 3: FE data hook — gửi param + client-side keep-row + snapshot

**Files:**
- Modify: `frontend/src/features/orders/use-orders-data.ts`

**Interfaces:**
- Consumes: `hasReturnSlip`/`setHasReturnSlip` từ `useOrdersFilters` (Task 2).
- Produces: `useOrdersData` return thêm `hasReturnSlip` + `setHasReturnSlip`; body request thêm `has_return_slip` (khớp param BE Task 1). Task 4 wiring dùng `orders.hasReturnSlip` / `orders.setHasReturnSlip`.

- [ ] **Step 1: Implement — mirror `hasAddress` tại MỌI vị trí.** Chạy `grep -n "hasAddress" frontend/src/features/orders/use-orders-data.ts` (~20 dòng) và chèn dòng `hasReturnSlip` tương ứng ngay sau TỪNG dòng `hasAddress` (giữ đúng dấu phẩy/cú pháp từng ngữ cảnh). Cụ thể theo nhóm:

- Overrides type (~80): `hasReturnSlip?: string;`
- Destructure từ filters hook (~350): `hasReturnSlip,` — và bổ sung `setHasReturnSlip,` cạnh `setHasAddress` trong cùng destructure (grep `setHasAddress` để thấy vị trí).
- `filterSnapshotRef` type (~426) `hasReturnSlip: string;` + init (~445) `hasReturnSlip: "",`
- `rowPassesActiveFilters` — chèn sau 2 dòng `f.hasAddress` (~546), TRƯỚC `return true;`:
  ```ts
    // "Phiếu trả hàng" nằm trên block recall — ngữ nghĩa khớp BE (slip khác rỗng).
    const rowHasReturnSlip = !!(
      row.recall?.return_slip_code && String(row.recall.return_slip_code).trim()
    );
    if (f.hasReturnSlip === "yes" && !rowHasReturnSlip) return false;
    if (f.hasReturnSlip === "no" && rowHasReturnSlip) return false;
  ```
- Value trim (~902): `const hasReturnSlipValue = (overrides?.hasReturnSlip ?? hasReturnSlip).trim();`
- `buildBody` (~943): `if (hasReturnSlipValue) body.has_return_slip = hasReturnSlipValue;`
- Chỗ set filter snapshot (vùng ~1059-1063, object chứa `hasAddress,`): `hasReturnSlip,`
- `clearFilters` overrides (~1821): `hasReturnSlip: "",`
- storedFilters memo object (~1881) + deps (~1901): `hasReturnSlip,`
- `currentFiltersRef` init (~1924), effect object (~1945), effect deps (~1965): `hasReturnSlip,`
- `switchFilterScope` isEmpty (~2001): `&& !snapshot.hasReturnSlip` (nối vào chuỗi `!snapshot.hasAddress;`)
- Apply target (~2047): `setHasReturnSlip(target.hasReturnSlip);`
- Persist payload (~2098): `hasReturnSlip,` — isEmpty (~2121): `&& !payload.hasReturnSlip` — deps (~2149): `hasReturnSlip,`
- Return object của hook (~2318): `hasReturnSlip,` — và `setHasReturnSlip,` cạnh `setHasAddress` trong cùng return (grep `setHasAddress`).

- [ ] **Step 2: Verify bằng typecheck + grep đối chiếu:**

```bash
cd frontend && npm run typecheck
grep -c "hasAddress" src/features/orders/use-orders-data.ts
grep -c "hasReturnSlip" src/features/orders/use-orders-data.ts
```

Expected: typecheck FAIL chỉ còn ở `routed-pages.tsx`/`OrdersPage` NẾU Task 4 chưa làm — nếu fail trong `use-orders-data.ts` thì sửa cho hết. Hai số grep chênh nhau tối đa 1-2 (hasAddress có 2 dòng comment/row.has_address không cần mirror).

- [ ] **Step 3: Commit:**

```bash
git add frontend/src/features/orders/use-orders-data.ts
git commit -m "feat(orders): send has_return_slip in list request body"
```

---

### Task 4: FE UI — dropdown panel + OrdersPage + wiring + i18n

**Files:**
- Modify: `frontend/src/features/orders/OrdersFilterPanel.tsx`
- Modify: `frontend/src/pages/OrdersPage.tsx`
- Modify: `frontend/src/app/routed-pages.tsx`, `frontend/src/app/RootLayout.tsx`, `frontend/src/App.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts`

**Interfaces:**
- Consumes: `orders.hasReturnSlip` / `orders.setHasReturnSlip` từ `useOrdersData` (Task 3); `loaded.hasReturnSlip` từ `StoredOrderFilters` (Task 2).

- [ ] **Step 1: i18n** — `frontend/src/i18n/messages/orders.ts`, chèn sau block `hasAddress` của TỪNG locale:

vi (sau dòng 58):
```ts
    "orders.filters.hasReturnSlip": "Phiếu trả hàng",
    "orders.filters.hasReturnSlipAll": "Tất cả",
    "orders.filters.hasReturnSlipYes": "Có phiếu trả",
    "orders.filters.hasReturnSlipNo": "Chưa có phiếu trả",
```

en (sau dòng ~278):
```ts
    "orders.filters.hasReturnSlip": "Return slip",
    "orders.filters.hasReturnSlipAll": "All",
    "orders.filters.hasReturnSlipYes": "Has return slip",
    "orders.filters.hasReturnSlipNo": "No return slip",
```

- [ ] **Step 2: OrdersFilterPanel.tsx** — 3 chỗ:

(a) Props type (sau `onCustomerReturnChange` ~124-125... đặt cạnh cặp customerReturn):
```ts
  hasReturnSlip: string;
  onHasReturnSlipChange: (value: string) => void;
```

(b) Destructure (~178 sau `onCustomerReturnChange,`): `hasReturnSlip,` `onHasReturnSlipChange,`

(c) JSX — chèn NGAY SAU block "Khách trả hàng" (sau `</div>` dòng 460, trước block "Ghi chú nội bộ"):
```tsx
        <div className="w-full">
          <label
            htmlFor="orders-filter-return-slip"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            {t("orders.filters.hasReturnSlip", "Phiếu trả hàng")}
          </label>
          <select
            id="orders-filter-return-slip"
            className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm focus:outline-hidden focus:border-ring focus:ring-3 focus:ring-ring/50"
            value={hasReturnSlip}
            onChange={(e) => onHasReturnSlipChange(e.target.value)}
          >
            <option value="">{t("orders.filters.hasReturnSlipAll", "Tất cả")}</option>
            <option value="yes">{t("orders.filters.hasReturnSlipYes", "Có phiếu trả")}</option>
            <option value="no">{t("orders.filters.hasReturnSlipNo", "Chưa có phiếu trả")}</option>
          </select>
        </div>
```

- [ ] **Step 3: OrdersPage.tsx** — chèn cạnh từng vị trí `hasAddress` (grep `hasAddress` trong file, ~10 chỗ):

- Props type (~120): `hasReturnSlip: string;` — (~142): `onHasReturnSlipChange: (value: string) => void;`
- `onApplyFilters` overrides type (~167): `hasReturnSlip: string;`
- Destructure (~296): `hasReturnSlip,` — (~317): `onHasReturnSlipChange,`
- Active-filter count (~442): `if (hasReturnSlip) n += 1;` + thêm `hasReturnSlip,` vào deps (~461)
- `ordersFilterKey` object (~486): `hasReturnSlip,` + deps (~509): `hasReturnSlip,`
- Handler — chèn sau `handleHasAddressChange` (~935):
```ts
  const handleHasReturnSlipChange = useCallback(
    (value: string) => {
      onHasReturnSlipChange(value);
      onApplyFilters({ hasReturnSlip: value }).catch((err) => {
        console.error("filter-[hasReturnSlip]", err);
        toast.error(t("orders.toast.fetchListFailed"));
      });
    },
    [onHasReturnSlipChange, onApplyFilters, t],
  );
```
- Truyền xuống panel (~1327-1328, cạnh `hasAddress={hasAddress}`):
```tsx
            hasReturnSlip={hasReturnSlip}
            onHasReturnSlipChange={handleHasReturnSlipChange}
```

- [ ] **Step 4: Wiring container** — chèn sau từng dòng `hasAddress` tương ứng:

- `frontend/src/app/routed-pages.tsx` (~100): `hasReturnSlip: orders.hasReturnSlip,` — (~122): `onHasReturnSlipChange: orders.setHasReturnSlip,`
- `frontend/src/app/RootLayout.tsx` (~124): `hasReturnSlip: loaded.hasReturnSlip,`
- `frontend/src/App.tsx` (~323 và ~400): `hasReturnSlip: loaded.hasReturnSlip,`

- [ ] **Step 5: Verify:**

```bash
cd frontend && npm run typecheck && npx eslint src/features/orders/OrdersFilterPanel.tsx src/pages/OrdersPage.tsx src/app/routed-pages.tsx src/app/RootLayout.tsx src/App.tsx src/features/orders/use-orders-data.ts && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx
```

Expected: typecheck PASS toàn dự án, eslint PASS, vitest PASS.

- [ ] **Step 6: Commit:**

```bash
git add frontend/src && git commit -m "feat(orders): return slip filter dropdown on orders/landing/koc pages"
```

---

### Task 5: Verification tổng + push + MR

- [ ] **Step 1: BE full-suite orders app** (lệnh Docker như Task 1 Step 2 nhưng path `apps/orders/tests -q`). Expected: pass trừ baseline fail đã biết (return-rate).
- [ ] **Step 2: FE test suite orders feature:** `cd frontend && npx vitest run src/features/orders`. Expected: pass trừ baseline fail đã biết.
- [ ] **Step 3: Cập nhật spec** — thêm dòng `**Tiến độ:** Hoàn thành <ngày> — BE 4 endpoint + FE 3 trang, tests xanh.` vào đầu file spec; commit `docs: mark return slip filter done`.
- [ ] **Step 4: Push + MR:**

```bash
git push -u origin feat/orders-return-slip-filter
```

Lấy link tạo MR từ output push (GitLab in sẵn `remote: ... merge_request url`), gửi cho user.
