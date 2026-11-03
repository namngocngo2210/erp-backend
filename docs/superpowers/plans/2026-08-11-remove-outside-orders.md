# Xóa trang Đơn gửi ngoài (Ngoại sàn) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gỡ bỏ hoàn toàn tính năng "Đơn gửi ngoài" — trang, route, endpoint, RBAC section, và hai cột DB `outside` / `shipping_form`.

**Architecture:** Xóa từ ngoài vào trong theo đúng chiều phụ thuộc: frontend trước (không còn ai gọi), rồi endpoint + RBAC, rồi model + migration. Thứ tự này giữ cho mọi commit trung gian đều chạy được, và migration phá dữ liệu là commit cuối cùng nên dễ dừng lại nếu đổi ý.

**Tech Stack:** React 19 + TypeScript + Vitest; Django 5 + DRF + PostgreSQL + pytest.

## Global Constraints

- Worktree riêng cho plan này (tạo ở Task 0), branch `chore/remove-outside-orders`, cắt từ **master mới nhất** — sau khi plan `2026-08-11-sales-nav-split.md` đã merge, vì cả hai đều sửa `frontend/src/app/modules.ts`.
- ⚠️ **Migration ở Task 5 KHÔNG HOÀN TÁC ĐƯỢC.** Dữ liệu `outside` và `shipping_form` của đơn gửi ngoài cũ mất vĩnh viễn. Bước dump CSV ở Task 5 là bắt buộc, không được bỏ qua.
- ⚠️ **Đơn cũ mang `order_type` bị gỡ khỏi `choices`** (`same_product`, `different_product`, `purchase`, `warranty_return`) vẫn giữ nguyên chuỗi trong DB — Django `TextChoices` không ràng buộc ở tầng DB — và sẽ **hiển thị mã thô** thay vì nhãn tiếng Việt. Đây là đánh đổi đã được chốt, không phải lỗi cần sửa.
- **KHÔNG xóa đơn hàng nào.** Chỉ xóa cột. Đơn gửi ngoài cũ ở lại bảng `orders` và tiếp tục hiển thị ở trang Đơn hàng chung — `list_orders` vốn không khóa `outside=False` nên chúng đã hiện ở đó từ trước, không có gì "lọt vào" chỗ mới.
- Giữ nguyên `SHIPPING_FORM_OPTIONS` trong `frontend/src/features/warranty-claims/journey-enums.ts` — trùng tên nhưng là enum riêng của luồng bảo hành, không liên quan `Order.shipping_form`.
- Giữ nguyên index `ord_no_address_created_idx` (`WHERE NOT has_address`) — thuộc tính năng khác.
- Repo không có CI — chạy test/lint tay. Lệnh test backend/frontend giống plan `2026-08-11-consulting-tab-sticky.md`, đổi worktree thành `desi-order-wt-no-outside` và `POSTGRES_DB` thành `desi_no_outside*`, `REDIS_CACHE_DB` 13.
- Baseline test đỏ SẴN trên master: frontend `RequireAuth.test.tsx` (2 test), `use-orders-data.realtime-upsert-filters.test.tsx` (5 test); backend `test_report_trends` (6 test).

---

## File Structure

Không có file nào bị xóa nguyên vẹn — tính năng này sống rải rác trong các file dùng chung, nên toàn bộ công việc là gỡ phần liên quan ra khỏi từng file. Hai file mới duy nhất là hai migration.

Danh sách file bị sửa:

**Frontend** — `app/routes.tsx`, `app/routed-pages.tsx`, `app/modules.ts`, `app/orders-tab.ts`, `app/RootLayout.tsx`, `App.tsx`, `lib/routes.ts`, `hooks/use-crm-websocket.ts`, `pages/OrdersPage.tsx`, `pages/RolesPage.tsx`, `features/orders/order-storage.ts`, `features/orders/order-types.ts`, `features/orders/order-payload.ts`, `features/orders/order-import-format.ts`, `features/orders/OrderFormDialog.tsx`, `features/orders/order-form/order-form-helpers.ts`, `features/orders/OrdersTable/OrderCell.tsx`, `features/orders/OrdersTable/useOrdersTableData.ts`, `features/orders/orders-page/use-orders-filter-options.ts`, `i18n/messages/topnav.ts`, và các file test tương ứng.

**Backend** — `apps/orders/models.py`, `apps/orders/repositories.py`, `apps/orders/urls.py`, `apps/orders/views.py`, `apps/orders/transport/views/order_views_impl/__init__.py`, `apps/orders/transport/views/order_views_impl/order_list_view.py`, `apps/orders/transport/views/order_views_impl/_view_helpers.py`, `apps/orders/application/services/order_service_commands.py`, `apps/orders/application/services/order_service_helpers.py`, `apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py`, `apps/users/permissions.py`, `apps/users/models.py`, và 2 migration mới.

---

### Task 0: Dựng worktree và chốt phạm vi bằng grep

**Files:**
- Không sửa file nào.

**Interfaces:**
- Consumes: không có.
- Produces: worktree `c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside` trên branch `chore/remove-outside-orders`; file `outside-inventory.txt` liệt kê mọi vị trí phải đụng tới.

- [ ] **Step 1: Xác nhận plan sidebar đã merge**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order && git pull --ff-only origin master && git log --oneline -5 master | grep -i "customer care\|nav-split"
```

Nếu không thấy commit tách sidebar trong master, **dừng lại** và merge MR đó trước — cả hai plan cùng sửa `frontend/src/app/modules.ts`, làm song song sẽ conflict.

- [ ] **Step 2: Tạo worktree anh em + junction**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order && git worktree add -b chore/remove-outside-orders ../desi-order-wt-no-outside master
```

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-no-outside\frontend\node_modules" -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

- [ ] **Step 3: Chụp danh sách vị trí cần xóa**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -rn "outside\|shipping_form\|shippingForm\|ShippingForm" backend/apps frontend/src --include=*.py --include=*.ts --include=*.tsx \
  | grep -vi "click.*outside\|clickOutside\|warranty-claims" > outside-inventory.txt && wc -l outside-inventory.txt
```

Giữ file này ngoài git (đừng `git add`). Nó là checklist đối chiếu ở Task 6 — cuối plan mọi dòng trong đó phải biến mất trừ các dòng thuộc `warranty-claims` và `multi-select.tsx` (`click outside`).

---

### Task 1: Gỡ route, section và nhánh điều hướng ở frontend

**Files:**
- Modify: `frontend/src/lib/routes.ts:7,51,100,126-128,136`
- Modify: `frontend/src/app/routes.tsx:63,125-139`
- Modify: `frontend/src/app/routed-pages.tsx:316-317` (xóa cả hàm `OutsideOrdersRoute`)
- Modify: `frontend/src/app/modules.ts` (xóa dòng section `outside-orders` khỏi module `sales`)
- Modify: `frontend/src/app/orders-tab.ts:23,40-44`
- Modify: `frontend/src/app/RootLayout.tsx:79,97-156`
- Modify: `frontend/src/App.tsx:99,367-374,458`
- Modify: `frontend/src/hooks/use-crm-websocket.ts:21`
- Modify: `frontend/src/pages/OrdersPage.tsx:392`
- Modify: `frontend/src/pages/RolesPage.tsx:71,110`
- Modify: `frontend/src/features/orders/order-storage.ts:165-167`
- Modify: `frontend/src/features/orders/OrdersTable/useOrdersTableData.ts:18`
- Modify: `frontend/src/i18n/messages/topnav.ts:27,69` (xóa khóa `topnav.tabs.outsideOrders`)
- Test: `frontend/src/lib/__tests__/routes.test.ts`, `frontend/src/app/__tests__/modules.test.ts`, `frontend/src/app/__tests__/nav-layout.test.ts`

**Interfaces:**
- Consumes: không có.
- Produces: type `AppSection` không còn nhánh `"outside-orders"`; `getOrderTabScope` nhận `section: "orders" | "landing-orders" | "koc-orders"`; `OrderExportSection` mất `"outside-orders"`; `FilterScope` mất `"outside" | "outside-delivered" | "outside-cancelled"`. Task 2 dựa trên các type đã hẹp lại này.

- [ ] **Step 1: Sửa test cho khớp trạng thái sau khi xóa**

Trong `frontend/src/lib/__tests__/routes.test.ts`:
- Xóa `"outside-orders"` khỏi mảng liệt kê section (dòng 18).
- Xóa dòng assert `outsideOrderDetailPath(12)` (dòng 77).
- Xóa `expect(parseOrdersView("/sales/outside-orders/5"))…` (dòng 83).
- Xóa `expect(sectionFromPath("/sales/outside-orders/7"))…` (dòng 95).
- Thêm một test khóa hành vi mới, đặt cuối `describe` chứa `sectionFromPath`:

```ts
  it("URL Đơn gửi ngoài đã gỡ -> rơi về section orders, không crash", () => {
    expect(sectionFromPath("/sales/outside-orders")).toBe("orders");
    expect(sectionFromPath("/sales/outside-orders/7")).toBe("orders");
  });
```

Trong `frontend/src/app/__tests__/modules.test.ts`:
- Xóa `"outside-orders"` khỏi mảng `ALL_SECTIONS` (dòng 16).
- Xóa nguyên `it("section hidden không làm module hiển thị, nhưng vẫn map trong SECTION_TO_MODULE")` (dòng 121-126) — không còn section ẩn nào.

Trong `frontend/src/app/__tests__/nav-layout.test.ts`:
- Xóa nguyên `it("section hidden (outside-orders) không xuất hiện trong nav dù đủ quyền")` (dòng 105-112).

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npx vitest run src/lib/__tests__/routes.test.ts src/app/__tests__/modules.test.ts src/app/__tests__/nav-layout.test.ts
```

Kỳ vọng: FAIL ở `URL Đơn gửi ngoài đã gỡ` (hiện `sectionFromPath` vẫn trả `"outside-orders"`) và ở `each section belongs to exactly one module` (`APP_MODULES` vẫn còn section đó).

- [ ] **Step 3: Gỡ khỏi `lib/routes.ts`**

Xóa 5 chỗ:
- Dòng 7: nhánh `| "outside-orders"` trong union `AppSection`.
- Dòng 51: entry `"outside-orders": "/sales/outside-orders",` trong `SECTION_TO_PATH`.
- Dòng 100: bỏ `|outside-orders` khỏi regex, còn `/^\/sales\/(?:orders|landing-orders|koc-orders)\/(\d+)\/?$/i`.
- Dòng 126-128: xóa cả comment và hàm `outsideOrderDetailPath`.
- Dòng 136: xóa dòng `if (/^\/sales\/outside-orders(\/|$)/.test(normalized)) return "outside-orders";`.

Sau khi xóa dòng 136, `sectionFromPath("/sales/outside-orders")` rơi xuống `return "orders"` mặc định ở cuối hàm — đúng như test ở Step 1 yêu cầu.

Kiểm tra thêm mảng `LEGACY_REDIRECTS` (quanh dòng 145-151): nếu có entry `{ from: "/outside-orders", to: "/sales/outside-orders" }` thì xóa luôn.

- [ ] **Step 4: Gỡ route và page component**

Trong `frontend/src/app/routes.tsx`: xóa `OutsideOrdersRoute` khỏi khối import (dòng 63) và xóa hai object route `sales/outside-orders` + `sales/outside-orders/:orderId` cùng comment của chúng (dòng 125-139).

Trong `frontend/src/app/routed-pages.tsx`: xóa comment dòng 316 và toàn bộ hàm `OutsideOrdersRoute` (từ `export function OutsideOrdersRoute()` tới dấu `}` đóng hàm).

- [ ] **Step 5: Gỡ section khỏi `modules.ts` và nhãn i18n**

Trong `frontend/src/app/modules.ts`, xóa dòng:

```ts
      { key: "outside-orders", labelKey: "topnav.tabs.outsideOrders", icon: Send, hidden: true },
```

Sau khi xóa, `Send` có thể thành import thừa. Kiểm tra:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -n "Send" frontend/src/app/modules.ts
```

Nếu chỉ còn dòng import thì xóa `Send,` khỏi khối import `lucide-react`.

Trong `frontend/src/i18n/messages/topnav.ts`, xóa hai dòng `"topnav.tabs.outsideOrders": …` (vi dòng 27, en dòng 69).

- [ ] **Step 6: Thu hẹp type và gỡ nhánh điều hướng**

Trong `frontend/src/app/orders-tab.ts`:
- Dòng 23: đổi chữ ký thành `section: "orders" | "landing-orders" | "koc-orders",`.
- Dòng 40-44: xóa cả khối `if (section === "outside-orders") { … }`.

Trong `frontend/src/features/orders/order-storage.ts` (dòng 165-167): xóa ba nhánh `| "outside"`, `| "outside-delivered"`, `| "outside-cancelled"` khỏi union `FilterScope`.

Trong `frontend/src/features/orders/OrdersTable/useOrdersTableData.ts` (dòng 18): đổi thành

```ts
export type OrderExportSection = "orders" | "landing-orders" | "koc-orders";
```

Trong `frontend/src/hooks/use-crm-websocket.ts` (dòng 21): đổi thành

```ts
const ORDER_LIST_SECTIONS = new Set(["orders", "landing-orders", "koc-orders"]);
```

Trong `frontend/src/app/RootLayout.tsx`, `frontend/src/App.tsx`, `frontend/src/pages/OrdersPage.tsx`: xóa mọi nhánh so sánh với `"outside-orders"` / `"outside"`. Đây là các biểu thức ba ngôi lồng nhau — sau khi xóa nhánh giữa phải đảm bảo biểu thức vẫn đúng cú pháp. Dùng `npx tsc --noEmit` ở Step 8 làm lưới an toàn; nếu một biểu thức ba ngôi chỉ còn một nhánh thì rút gọn thành giá trị đó.

Trong `frontend/src/pages/RolesPage.tsx`: xóa dòng 71 (`"outside-orders": "Đơn gửi ngoài",`) và mục `"outside-orders",` trong mảng ở dòng 110.

- [ ] **Step 7: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npx vitest run src/lib/__tests__/routes.test.ts src/app/__tests__/modules.test.ts src/app/__tests__/nav-layout.test.ts
```

Kỳ vọng: PASS cả ba file.

- [ ] **Step 8: Typecheck bắt phần còn sót**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npx tsc --noEmit
```

Kỳ vọng: 0 lỗi. Mọi lỗi ở đây đều là chỗ còn tham chiếu section/scope đã xóa — sửa hết rồi chạy lại.

- [ ] **Step 9: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git add -A frontend/src
git commit -m "chore(orders): remove outside orders route and section from frontend"
```

---

### Task 2: Gỡ chế độ tạo đơn gửi ngoài và cột `shipping_form` khỏi frontend

**Files:**
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx:26,61,132,179,792,800-820`
- Modify: `frontend/src/features/orders/order-form/order-form-helpers.ts:109,142-150,178`
- Modify: `frontend/src/features/orders/order-types.ts:182,256,359,415,457-464,519`
- Modify: `frontend/src/features/orders/order-payload.ts:40-41`
- Modify: `frontend/src/features/orders/order-import-format.ts:24,94`
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx:676-677`
- Modify: `frontend/src/features/orders/orders-page/use-orders-filter-options.ts:5,82`
- Modify: `frontend/src/features/orders/use-orders-data.ts:132-134,248,597`
- Modify: `frontend/src/features/orders/use-orders-table.ts:784`

**Interfaces:**
- Consumes: type đã hẹp từ Task 1.
- Produces: `OrderColumnKey` không còn `"shipping_form"`; `ORDER_TYPE_VALUES` là danh sách order type duy nhất (không còn `OUTSIDE_ORDER_TYPE_VALUES`); payload tạo/sửa đơn không còn `outside` và `shipping_form`. Task 3 gỡ phía backend tương ứng.

- [ ] **Step 1: Gỡ chế độ `outsideMode` khỏi form đơn**

Trong `frontend/src/features/orders/OrderFormDialog.tsx`:
- Xóa `SHIPPING_FORM_VALUES` khỏi import (dòng 26) và `OUTSIDE_ORDER_TYPE_VALUES` khỏi import (dòng 61).
- Xóa prop `outsideMode?: boolean;` (dòng 132) và tham số `outsideMode = false,` (dòng 179).
- Dòng 792: đổi `{(outsideMode ? OUTSIDE_ORDER_TYPE_VALUES : ORDER_TYPE_VALUES).map((v) => (` thành `{ORDER_TYPE_VALUES.map((v) => (`.
- Xóa toàn bộ khối `{outsideMode && ( … )}` (dòng 800-820) — đó là ô chọn "Hình thức gửi".

Trong `frontend/src/features/orders/order-form/order-form-helpers.ts`:
- Xóa `shipping_form: "",` (dòng 109) và `shipping_form: order.shipping_form ?? "",` (dòng 178).
- Xóa toàn bộ `export const OUTSIDE_ORDER_TYPE_VALUES = [ … ];` (dòng 142-150).

Trong `frontend/src/features/orders/use-orders-data.ts`:
- Xóa `outsideModeRef` và setter của nó (dòng 132-134) cùng entry `isOutside: () => outsideModeRef.current,` (dòng 248).
- Dòng 597: gỡ `shipping_form` khỏi danh sách trục lọc trong comment và code.

Trong `frontend/src/features/orders/use-orders-table.ts` (dòng 784): xóa nhánh chọn endpoint `/api/orders/outside`. Nếu sau khi xóa chỉ còn một nhánh thì rút gọn biểu thức.

- [ ] **Step 2: Gỡ cột `shipping_form` khỏi bảng đơn**

Trong `frontend/src/features/orders/order-types.ts`, xóa 6 chỗ:
- Dòng 182 và 519: field `shipping_form?: string;` trong hai type đơn.
- Dòng 256: nhánh `| "shipping_form"` trong union `OrderColumnKey`.
- Dòng 359: entry `{ key: "shipping_form", label: "orders.columns.shippingForm" },`.
- Dòng 415: entry `shipping_form: "shipping_form",` trong map field.
- Dòng 457-464: cả `SHIPPING_FORM_VALUES` và type `ShippingFormValue`.
- Tìm và xóa luôn nhánh `outside?: boolean;` (dòng 181 và 518).

Trong `frontend/src/features/orders/OrdersTable/OrderCell.tsx` (dòng 676-677): xóa cả `case "shipping_form": { … }`.

Trong `frontend/src/features/orders/orders-page/use-orders-filter-options.ts`: xóa `SHIPPING_FORM_VALUES` khỏi import (dòng 5) và xóa cả biến/memo dùng nó (dòng 82) cùng chỗ tiêu thụ biến đó trong cùng file.

Trong `frontend/src/features/orders/order-payload.ts` (dòng 40-41): xóa `outside?: boolean;` và `shipping_form?: string;`.

Trong `frontend/src/features/orders/order-import-format.ts`: xóa `shipping_form: values.shipping_form ?? "",` (dòng 24) và cả `export const OUTSIDE_COLUMN_KEYS = ["shipping_form"];` (dòng 94) cùng mọi nơi import nó.

Tìm và xóa khóa i18n của cột:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -rn "orders.columns.shippingForm\|shippingForm\." frontend/src/i18n
```

Xóa các dòng tìm được (cả `vi` lẫn `en`).

- [ ] **Step 3: Typecheck để tìm phần còn sót**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npx tsc --noEmit
```

Kỳ vọng: 0 lỗi. Sửa hết rồi chạy lại.

- [ ] **Step 4: Chạy suite frontend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npm run test:ci
```

Kỳ vọng: đúng 2 file đỏ / 7 test đỏ baseline. Test nào đỏ vì còn dựng dữ liệu `outside`/`shipping_form` thì gỡ phần đó khỏi test — đó là fixture của tính năng đã xóa, không phải hồi quy.

- [ ] **Step 5: Lint**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npm run lint:strict
```

Kỳ vọng: sạch. Lỗi hay gặp: import thừa sau khi xóa (`Send`, `SHIPPING_FORM_VALUES`, `OUTSIDE_ORDER_TYPE_VALUES`).

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git add -A frontend/src
git commit -m "chore(orders): remove outside order form mode and shipping form column"
```

---

### Task 3: Gỡ endpoint và RBAC section ở backend

**Files:**
- Modify: `backend/apps/orders/urls.py:62,86`
- Modify: `backend/apps/orders/views.py:55,129`
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py:73,141`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py:192-225`
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py:57,92`
- Modify: `backend/apps/users/permissions.py:4`
- Modify: `backend/apps/users/models.py:10`
- Modify: `backend/apps/orders/repositories.py:136-137,783-787`
- Test: `backend/apps/users/tests/test_rbac_sections.py`

**Interfaces:**
- Consumes: frontend đã không còn gọi `/api/orders/outside` (Task 1).
- Produces: `ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders")`; `VALID_SECTIONS` không còn `"outside-orders"`; `_apply_list_filters` không còn hiểu khóa `outside`. Task 4 gỡ model field.

- [ ] **Step 1: Viết test đỏ cho RBAC**

Thêm vào cuối `backend/apps/users/tests/test_rbac_sections.py`:

```python
class OutsideOrdersSectionRemovedTests(TestCase):
    """Section `outside-orders` đã bị gỡ (2026-08-11) — không còn trong catalog
    quyền, không role nào giữ được nó, và endpoint cũ trả 404."""

    def test_not_in_valid_sections(self):
        self.assertNotIn("outside-orders", VALID_SECTIONS)

    def test_not_in_order_sections(self):
        self.assertNotIn("outside-orders", ORDER_SECTIONS)

    def test_no_permission_row_left(self):
        self.assertFalse(Permission.objects.filter(section="outside-orders").exists())
        self.assertFalse(RoleSectionScope.objects.filter(section="outside-orders").exists())

    def test_endpoint_gone(self):
        client = APIClient()
        client.force_authenticate(user=make_admin("admin-no-outside"))
        response = client.post("/api/orders/outside", {"limit": "10", "offset": "0"}, format="json")
        self.assertEqual(response.status_code, 404)
```

Kiểm tra khối import ở đầu file trước khi thêm; cần `VALID_SECTIONS` (từ `apps.users.models`), `ORDER_SECTIONS` (từ `apps.users.permissions`), `Permission`, `RoleSectionScope`, `APIClient`, `make_admin`, `TestCase`.

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_t3 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=13 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/users/tests/test_rbac_sections.py::OutsideOrdersSectionRemovedTests -q --create-db'
```

Kỳ vọng: FAIL cả 4 test — section vẫn còn trong hai hằng, vẫn có `Permission` row từ seed, endpoint vẫn trả 200.

- [ ] **Step 3: Gỡ endpoint**

Trong `backend/apps/orders/transport/views/order_views_impl/order_list_view.py`: xóa toàn bộ khối `@extend_schema(operation_id="orders_outside_list", …)` cùng hàm `list_outside_orders` bên dưới (dòng 192-225). Giữ nguyên `_run_orders_list` — `list_orders` vẫn dùng.

Trong `backend/apps/orders/transport/views/order_views_impl/__init__.py`: xóa `list_outside_orders` khỏi khối import (dòng 73) và khỏi `__all__` (dòng 141).

Trong `backend/apps/orders/views.py`: xóa `list_outside_orders` khỏi import (dòng 55) và khỏi `__all__` (dòng 129).

Trong `backend/apps/orders/urls.py`: xóa `list_outside_orders` khỏi import (dòng 62) và xóa dòng `path("outside", list_outside_orders, name="orders-outside-list"),` (dòng 86).

- [ ] **Step 4: Gỡ RBAC section và bộ lọc**

Trong `backend/apps/users/permissions.py` (dòng 4):

```python
ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders")
```

Trong `backend/apps/users/models.py`: xóa dòng `"outside-orders",` khỏi `VALID_SECTIONS` (dòng 10).

Trong `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py`: xóa dòng `"outside-orders": Q(outside=True),` khỏi `_SECTION_ORDER_IDENTITY_Q` (dòng 92) và dòng comment nhắc tới nó (dòng 57).

Trong `backend/apps/orders/repositories.py`: xóa khối `if section == "outside-orders": return {"outside": "true"}` trong `_section_filter_overrides` (dòng 136-137) và toàn bộ khối xử lý `outside_filter` trong `_apply_list_filters` (dòng 783-787).

- [ ] **Step 5: Viết data-migration gỡ quyền khỏi role**

Tạo file `backend/apps/users/migrations/0034_remove_outside_orders_section.py`:

```python
"""Gỡ section `outside-orders` khỏi catalog quyền RBAC.

Trang Đơn gửi ngoài đã bị xóa (2026-08-11). Xóa `Permission` của section này
cascade luôn `rbac_role_permissions`, nên role nào đang được cấp cũng mất quyền
trỏ vào hư không. `RoleSectionScope` phải xóa riêng vì không có FK tới Permission.

reverse để no-op: seed lại quyền cho một trang không còn tồn tại là vô nghĩa,
và migration ngược chỉ cần chạy được để không chặn `migrate <app> <number>`.
"""
from django.db import migrations

SECTION = "outside-orders"


def remove_section(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    RoleSectionScope = apps.get_model("users", "RoleSectionScope")
    Permission.objects.filter(section=SECTION).delete()
    RoleSectionScope.objects.filter(section=SECTION).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0033_seed_sale_assignment_permissions"),
    ]

    operations = [
        migrations.RunPython(remove_section, noop),
    ]
```

- [ ] **Step 6: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_t3 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=13 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/users -q --create-db'
```

Kỳ vọng: PASS toàn bộ app `users`.

- [ ] **Step 7: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git add backend/apps/orders backend/apps/users
git commit -m "chore(orders): remove outside orders endpoint and rbac section"
```

---

### Task 4: Gỡ field khỏi service và serializer

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py:992`
- Modify: `backend/apps/orders/application/services/order_service_helpers.py` (chỉ các dòng gán `outside` / `shipping_form`; **không** đụng `has_address` ở dòng 118)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py:175`
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py:323`

**Interfaces:**
- Consumes: endpoint đã gỡ (Task 3).
- Produces: không còn code Python nào đọc/ghi `Order.outside` hay `Order.shipping_form` — điều kiện bắt buộc để Task 5 drop cột an toàn.

- [ ] **Step 1: Gỡ khỏi luồng tạo đơn**

Trong `backend/apps/orders/application/services/order_service_commands.py` (dòng 992): xóa dòng `outside=bool(payload.get("outside", False)),`.

Kiểm tra ngay dưới có dòng gán `shipping_form` không:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -n "outside\|shipping_form" backend/apps/orders/application/services/order_service_commands.py
```

Xóa mọi dòng tìm được.

⚠️ Trong `order_service_helpers.py` dòng 118 có `has_address=order.has_address,` — **giữ nguyên dòng đó**. `has_address` là cột của tính năng khác (cờ ingest cho tab Tư vấn), tên gần giống nhưng không liên quan. Chỉ xóa các dòng gán `outside=` / `shipping_form=`:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -n "\boutside\b\|shipping_form" backend/apps/orders/application/services/order_service_helpers.py
```

Xóa mọi dòng lệnh trên tìm được (mẫu `\boutside\b` không khớp `has_address`).

- [ ] **Step 2: Gỡ khỏi serializer và bộ lọc**

Trong `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py`: xóa `"shipping_form",` khỏi danh sách `fields` (dòng 175). Kiểm tra cả `"outside",` trong cùng danh sách và xóa nếu có.

Trong `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py` (dòng 323): xóa dòng `"shipping_form": _parse_multi_query_values(request, "shipping_form"),`.

Trong `backend/apps/orders/repositories.py`: xóa khối xử lý `shipping_form_filter` (dòng 765-770).

- [ ] **Step 3: Xác nhận không còn code nào đụng hai cột**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -rn "\boutside\b\|shipping_form" backend/apps --include=*.py | grep -v "/migrations/" | grep -v "/tests/"
```

Kỳ vọng: không có kết quả. Nếu còn, xóa nốt rồi chạy lại.

- [ ] **Step 4: Dọn fixture test còn dùng hai cột**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -rn "\boutside\b\|shipping_form" backend/apps --include=*.py | grep "/tests/"
```

Xóa các tham số `outside=…` / `shipping_form=…` trong fixture, và xóa nguyên test nào chỉ tồn tại để khóa hành vi của trang Đơn gửi ngoài.

- [ ] **Step 5: Chạy suite orders**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_t4 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=13 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders -q --create-db'
```

Kỳ vọng: PASS (trừ baseline `test_report_trends`).

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git add backend/apps/orders
git commit -m "chore(orders): stop reading and writing outside and shipping form fields"
```

---

### Task 5: Drop cột DB (KHÔNG HOÀN TÁC ĐƯỢC)

**Files:**
- Modify: `backend/apps/orders/models.py:43-50` (bỏ `ShippingForm`), `:33-40` (thu hẹp `OrderType`), `:363-366` (bỏ 2 field), `:429-434` (bỏ index)
- Create: `backend/apps/orders/migrations/0119_remove_outside_shipping_form.py`

**Interfaces:**
- Consumes: không còn code nào đọc/ghi hai cột (Task 4 Step 3 đã xác nhận).
- Produces: bảng `orders` không còn cột `outside`, `shipping_form`, không còn index `ord_outside_cr_idx`.

- [ ] **Step 1: Dump dữ liệu sắp mất, trên PROD**

Chạy **trước** khi apply migration lên prod, giữ file CSV lại:

```sql
SELECT count(*) FROM orders WHERE outside;
SELECT count(*) FROM orders WHERE shipping_form <> '';
\copy (SELECT id, order_code, invoice_code, outside, shipping_form, order_type, created_at FROM orders WHERE outside OR shipping_form <> '') TO 'outside_orders_backup_20260811.csv' CSV HEADER
```

Terminal SSH prod cắt dòng dài >110 ký tự — chạy từng lệnh một, đừng dán cả khối.

- [ ] **Step 2: Thu hẹp model**

Trong `backend/apps/orders/models.py`:

Xóa toàn bộ class `ShippingForm` (dòng 43-50).

Thu hẹp `OrderType` (dòng 33-40) còn:

```python
class OrderType(models.TextChoices):
    NORMAL = "normal", "Normal"
    NO_RETURN = "no_return", "No Return"
    HAS_RETURN = "has_return", "Has Return"
```

Xóa hai field và comment của chúng (dòng 361-366):

```python
    outside = models.BooleanField(default=False, db_default=False)
    shipping_form = models.CharField(
        max_length=30, choices=ShippingForm.choices, blank=True, default="", db_default=""
    )
```

Xóa index `ord_outside_cr_idx` cùng comment (dòng 429-434).

- [ ] **Step 3: Sinh migration**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_mig -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=13 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations orders --name remove_outside_shipping_form
```

File sinh ra phải là `0119_remove_outside_shipping_form.py` và chứa đúng 4 operation, theo thứ tự: `RemoveIndex(model_name="order", name="ord_outside_cr_idx")`, `RemoveField(model_name="order", name="outside")`, `RemoveField(model_name="order", name="shipping_form")`, `AlterField(model_name="order", name="order_type", …)`.

Mở file kiểm tra. Nếu Django sinh thiếu `RemoveIndex` hoặc sinh thêm operation lạ (do model drift sẵn có), sửa tay cho đúng 4 operation trên — **không** để migration này mang theo thay đổi của feature khác.

Thêm docstring vào đầu file migration:

```python
"""Xóa tính năng Đơn gửi ngoài — drop cột `outside` và `shipping_form`.

KHÔNG HOÀN TÁC ĐƯỢC: dữ liệu hai cột mất vĩnh viễn. Dump CSV trước khi chạy
(xem docs/superpowers/plans/2026-08-11-remove-outside-orders.md, Task 5 Step 1).

`AlterField` trên `order_type` chỉ đổi `choices` (metadata Python), không đụng
dữ liệu — đơn cũ mang `same_product` / `different_product` / `purchase` /
`warranty_return` giữ nguyên chuỗi trong DB và sẽ hiển thị mã thô thay vì nhãn
tiếng Việt. Đánh đổi đã được chốt.
"""
```

- [ ] **Step 4: Xác nhận không còn drift**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_mig -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=13 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations --check --dry-run
```

Kỳ vọng: `No changes detected`.

- [ ] **Step 5: Chạy toàn bộ suite backend**

Suite dài (~19 phút) — chạy nền, không để shell nền của harness giết nửa chừng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && MSYS_NO_PATHCONV=1 docker run -d --name desi-no-outside-suite --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_no_outside_full -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=14 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest -q --create-db'
```

```bash
docker wait desi-no-outside-suite && docker logs desi-no-outside-suite | tail -40 && docker rm desi-no-outside-suite
```

Kỳ vọng: chỉ còn baseline `test_report_trends` (6 test) đỏ.

- [ ] **Step 6: Lint backend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Kỳ vọng: `All checks passed!`. **Đừng tin ruff khi nó bảo xoá một import trông như "unused"** — `grep -n "import \*" <file>` trước; repo này từng có 8 test ERROR lúc collect vì xoá nhầm import được star re-export.

- [ ] **Step 7: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git add backend/apps/orders/models.py backend/apps/orders/migrations/0119_remove_outside_shipping_form.py
git commit -m "chore(orders): drop outside and shipping_form columns"
```

---

### Task 6: Kiểm chứng cuối và đẩy MR

**Files:**
- Không sửa file nguồn trừ khi phát hiện sót.

**Interfaces:**
- Consumes: kết quả Task 1-5.
- Produces: nhánh đã push, link MR kèm hướng dẫn chạy migration trên prod.

- [ ] **Step 1: Đối chiếu với danh sách chụp ở Task 0**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -rn "outside\|shipping_form\|shippingForm\|ShippingForm\|outsideOrders" backend/apps frontend/src --include=*.py --include=*.ts --include=*.tsx \
  | grep -vi "click.*outside\|clickOutside\|warranty-claims" | grep -v "/migrations/"
```

Kỳ vọng: không có kết quả. Mọi dòng còn lại phải giải thích được — nếu không, xóa nốt.

- [ ] **Step 2: Cập nhật `schema.yaml`**

Endpoint `/api/orders/outside` và field `shipping_form` biến mất khỏi API. Tìm và xóa các khối tương ứng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside && grep -n "orders_outside_list\|OutsideOrderList\|shipping_form" backend/schema.yaml
```

Xóa đúng các khối tìm được, giữ nguyên thụt lề YAML. **Không regen toàn file** — `schema.yaml` đang lệch sẵn ~444 dòng so với code, regen sẽ kéo nhiễu của feature khác vào MR này. Sau đó sinh lại type FE:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside/frontend && npm run gen:api && npx tsc --noEmit
```

- [ ] **Step 3: Kiểm tra bằng mắt trên app đang chạy**

Trên stack dev local (port 8080), đăng nhập `admin` / `admin123`:

1. Sidebar không có "Đơn gửi ngoài" ở bất kỳ nhóm nào.
2. Vào thẳng `http://localhost:8080/sales/outside-orders` → **không trắng trang, không crash**; rơi về trang Đơn hàng chung hoặc màn hình không có quyền.
3. Trang **Quản trị → Phân quyền**: không còn dòng "Đơn gửi ngoài" trong danh sách section.
4. Mở dialog **Tạo đơn** ở trang Đơn hàng chung: ô "Loại đơn" chỉ còn 3 lựa chọn (Normal / No Return / Has Return), **không có** ô "Hình thức gửi".
5. Bảng đơn: mở bộ chọn cột, không còn cột "Hình thức gửi"; bộ lọc không còn trục đó.
6. Đơn gửi ngoài cũ vẫn mở được ở trang chi tiết, không lỗi 500.

- [ ] **Step 4: Push và tạo MR**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-no-outside
git push -u origin chore/remove-outside-orders \
  -o merge_request.create \
  -o merge_request.title="chore(orders): remove outside orders feature including db columns" \
  -o merge_request.remove_source_branch
```

Mô tả MR phải nêu rõ, vì đây là MR có migration phá dữ liệu:

- **Migration `orders/0119` KHÔNG hoàn tác được.** Bắt buộc chạy bước dump CSV (Task 5 Step 1) trước, giữ file lại. Chạy vào **giờ thấp điểm**.
- Migration `users/0034` xóa `Permission` + `RoleSectionScope` của section `outside-orders`; role nào đang được cấp sẽ mất mục đó khỏi màn hình phân quyền.
- Đơn cũ mang `order_type` đã gỡ khỏi `choices` sẽ hiển thị **mã thô** (`same_product`, `different_product`, `purchase`, `warranty_return`) thay vì nhãn tiếng Việt.
- Đơn gửi ngoài cũ **không bị xóa**, chúng ở lại trang Đơn hàng chung — nơi chúng vốn đã hiển thị từ trước vì `list_orders` chưa bao giờ khóa `outside=False`.

Lấy link MR từ output lệnh push và gửi cho người yêu cầu.

- [ ] **Step 5: Ghi chú vận hành sau khi merge**

Khi deploy lên prod, thứ tự bắt buộc:

1. Dump CSV (Task 5 Step 1) — **trước** khi deploy code mới.
2. Deploy code + chạy `migrate`.
3. Kiểm tra lại app: sidebar, dialog tạo đơn, trang phân quyền.

Bẫy đã gặp trên prod: `docker compose pull` kéo cả image core không mong muốn, và recreate nhiều service cùng lúc gây trùng tên container. Recreate từng service một, dùng `--no-deps`.
