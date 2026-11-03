# KOC/KOL Orders Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm trang "Đơn hàng KOC/KOL" hoạt động giống trang Đơn hàng thường nhưng dữ liệu luôn bị khóa ở kênh `Channel.id = 15` ("hàng mẫu koc") tại server.

**Architecture:** Mirror pattern trang Landing — endpoint backend riêng `POST /api/orders/koc` (bản sao mỏng của `list_orders`, ép `channel_id=15`) + section frontend mới `koc-orders` tái dùng component `OrdersPage` bằng cách nới rộng các nhánh `section === "orders"` trong `App.tsx`.

**Tech Stack:** Django REST Framework (backend), React + TypeScript + Vite + Vitest (frontend).

---

## File Structure

**Backend:**
- Modify `backend/config/settings_parts/base.py` — thêm setting `KOC_CHANNEL_ID = 15`.
- Modify `backend/apps/orders/repositories.py` — thêm filter `channel_ids` trong `_apply_list_filters`.
- Modify `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` — thêm view `list_koc_orders`.
- Modify `backend/apps/orders/transport/views/order_views_impl/__init__.py` — export `list_koc_orders`.
- Modify `backend/apps/orders/urls.py` — route `koc`.
- Create `backend/apps/orders/tests/test_koc_orders_api.py` — test view.

**Frontend:**
- Modify `frontend/src/lib/routes.ts` — `AppSection`, path, `sectionFromPath`.
- Modify `frontend/src/features/orders/use-orders-data.ts` — `FilterScope`, KOC endpoint mode.
- Modify `frontend/src/App.tsx` — orchestration + render branches.
- Modify `frontend/src/components/Sidebar.tsx` — nav item.
- Modify `frontend/src/i18n/messages.ts` — i18n keys (vi + en).
- Create `frontend/src/lib/routes.koc.test.ts` — unit test cho `sectionFromPath`.

## Test Commands

**Backend** (pytest hỏng do BOM — dùng Django runner; cần Postgres + Redis):
```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order \
  POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order \
  REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD= \
  .venv/bin/python manage.py test apps.orders.tests.test_koc_orders_api -v2
```
(Nếu chưa có DB: chạy 2 container postgres:16-alpine + redis:7-alpine trên localhost theo memory `running-backend-tests`.)

**Frontend:**
```bash
cd frontend && npx vitest run src/lib/routes.koc.test.ts
```

---

## Task 1: Backend — setting KOC_CHANNEL_ID + filter channel_ids

**Files:**
- Modify: `backend/config/settings_parts/base.py`
- Modify: `backend/apps/orders/repositories.py` (vùng `_apply_list_filters`, ngay sau block `channel` tại dòng 220-225)
- Test: `backend/apps/orders/tests/test_koc_orders_api.py`

- [ ] **Step 1: Thêm setting KOC_CHANNEL_ID**

Mở `backend/config/settings_parts/base.py`, thêm vào cuối file (hoặc cạnh các business constant nếu có):

```python
# Kênh bán hàng "hàng mẫu koc" — dùng để khóa scope trang Đơn hàng KOC/KOL.
KOC_CHANNEL_ID = int(os.environ.get("KOC_CHANNEL_ID", "15"))
```

> Nếu `os` chưa được import trong file này, dùng giá trị literal `KOC_CHANNEL_ID = 15` thay vì đọc env.

- [ ] **Step 2: Thêm filter `channel_ids` vào repository**

Trong `backend/apps/orders/repositories.py`, ngay SAU block filter `channel` (kết thúc ở dòng 225, trước `product_filter = filters.get("product")`), chèn:

```python
        channel_ids = filters.get("channel_ids")
        if channel_ids:
            channel_id_values = channel_ids if isinstance(channel_ids, list) else [channel_ids]
            parsed_channel_ids = []
            for value in channel_id_values:
                try:
                    parsed_channel_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            if parsed_channel_ids:
                queryset = queryset.filter(channel_id__in=parsed_channel_ids)
            else:
                queryset = queryset.none()
```

- [ ] **Step 3: Viết test thất bại cho view (định nghĩa hành vi mong muốn)**

Tạo `backend/apps/orders/tests/test_koc_orders_api.py`:

```python
"""Verify /api/orders/koc chỉ trả đơn của kênh KOC (Channel.id=KOC_CHANNEL_ID)."""

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.constants import DepartmentID
from apps.users.models import Department, User, UserRole


class KocOrdersApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sales_dept = Department.objects.create(
            id=DepartmentID.SALES, name="Sales", allowed_sections=["orders"]
        )
        self.ads_dept = Department.objects.create(
            id=DepartmentID.ADS, name="Ads", allowed_sections=["orders"]
        )
        self.admin = User.objects.create_user(
            username="admin-koc", password="x", role=UserRole.ADMIN
        )

        # Kênh KOC phải có đúng id = settings.KOC_CHANNEL_ID.
        self.koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hàng mẫu koc"
        )
        self.other_channel = Channel.objects.create(code="lp-a", name="Landing A")

        self.customer = Customer.objects.create(
            full_name="Khach KOC",
            phone_raw="0911111111",
            phone_normalized="0911111111",
        )
        self.koc_order = Order.objects.create(
            channel=self.koc_channel, customer=self.customer,
            name="KOC Order", phone="0911111111", shipping_code="SHIPKOC",
        )
        self.other_order = Order.objects.create(
            channel=self.other_channel, customer=self.customer,
            name="Other Order", phone="0911111111", shipping_code="SHIPOTHER",
        )

    def _list(self, *, user):
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/orders/koc", {"limit": "100", "offset": "0"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_returns_only_koc_channel_orders(self):
        self.assertEqual(self._list(user=self.admin), {self.koc_order.id})

    def test_ads_staff_without_koc_assignment_sees_nothing(self):
        ads_staff = User.objects.create_user(
            username="ads-koc", password="x",
            role=UserRole.STAFF, department=self.ads_dept,
        )
        # Gán kênh khác (lp-a), không gán kênh koc.
        UserChannel.objects.create(
            user=ads_staff, channel=self.other_channel, is_active=True
        )
        self.assertEqual(self._list(user=ads_staff), set())
```

- [ ] **Step 4: Chạy test, xác nhận FAIL**

Run: lệnh backend test ở mục **Test Commands** ở trên.
Expected: FAIL — route `/api/orders/koc` chưa tồn tại (404) → assertion `status_code == 200` fail. (View sẽ thêm ở Task 2.)

- [ ] **Step 5: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/orders/repositories.py backend/apps/orders/tests/test_koc_orders_api.py
git commit -m "Add KOC channel setting, channel_ids filter, and failing KOC orders API test"
```

---

## Task 2: Backend — view list_koc_orders + URL + export

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (thêm view sau `list_orders`, trước `list_landing_orders`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py:50,83-85`
- Modify: `backend/apps/orders/urls.py:34,49-50`

- [ ] **Step 1: Thêm view `list_koc_orders`**

Trong `order_list_view.py`, thêm import settings ở đầu file (sau `import time`):

```python
from django.conf import settings
```

Thêm view (đặt ngay sau hàm `list_orders`, kết thúc ở dòng 77):

```python
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def list_koc_orders(request):
    """Danh sách đơn KOC/KOL — luôn khóa ở kênh Channel.id=settings.KOC_CHANNEL_ID."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    sort_value = _parse_sort(request, default="newest")
    filters = {
        "q": source.get("q", ""),
        "status": _parse_multi_query_values(request, "status"),
        "channel": _parse_multi_query_values(request, "channel"),
        "product": _parse_multi_query_values(request, "product"),
        "staff": _parse_multi_query_values(request, "staff"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "sales_status": _parse_multi_query_values(request, "sales_status"),
        "kiotviet_channel": _parse_multi_query_values(request, "kiotviet_channel"),
        "branch_id": _parse_multi_query_values(request, "branch_id"),
        "invoice_status": _parse_multi_query_values(request, "invoice_status"),
        "has_shipping_code": source.get("has_shipping_code", ""),
        "has_invoice_code": source.get("has_invoice_code", ""),
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "kiotviet_channel_null": source.get("kiotviet_channel_null", ""),
        "show_orders": source.get("show_orders", ""),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # Khóa cứng scope kênh KOC, không phụ thuộc client gửi gì.
        "channel_ids": [settings.KOC_CHANNEL_ID],
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    filters, scope_empty = _apply_user_channel_scope(filters, request.user)
    if scope_empty:
        return _empty_orders_response()

    try:
        queryset, total_count, offset, limit = OrderRepository.list_orders(filters)
        if sort_value == "oldest":
            queryset = queryset.order_by("created_at", "id")
        else:
            queryset = queryset.order_by("-created_at", "-id")
        page = list(queryset[offset : offset + limit])
        context = _build_order_list_context(request.user, page)
        serializer = OrderSerializer(page, many=True, context=context)
        data = {"results": serializer.data, "count": total_count}
    except Exception:
        logger.exception("Failed to query/serialize KOC orders list, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách đơn KOC. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)
```

- [ ] **Step 2: Export view trong `__init__.py`**

Sửa dòng 50 của `backend/apps/orders/transport/views/order_views_impl/__init__.py`:

```python
from .order_list_view import list_koc_orders, list_landing_orders, list_orders
```

Thêm `"list_koc_orders",` vào danh sách `__all__` (giữ thứ tự alphabet, đặt trước `"list_landing_orders",` ở dòng 83):

```python
    "list_koc_orders",
    "list_landing_orders",
```

- [ ] **Step 3: Đăng ký URL**

Trong `backend/apps/orders/urls.py`, thêm `list_koc_orders` vào import (cạnh `list_landing_orders` ở dòng 34):

```python
    list_landing_orders,
    list_koc_orders,
```

Thêm route ngay sau dòng 50 (`path("landing", ...)`):

```python
    path("koc", list_koc_orders, name="orders-koc-list"),
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: lệnh backend test ở mục **Test Commands**.
Expected: PASS — `test_returns_only_koc_channel_orders` và `test_ads_staff_without_koc_assignment_sees_nothing` đều xanh.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_list_view.py backend/apps/orders/transport/views/order_views_impl/__init__.py backend/apps/orders/urls.py
git commit -m "Add list_koc_orders endpoint scoped to KOC channel"
```

---

## Task 3: Frontend — routing cho section koc-orders

**Files:**
- Modify: `frontend/src/lib/routes.ts:1,8-27,50-62`
- Test: `frontend/src/lib/routes.koc.test.ts`

- [ ] **Step 1: Viết test thất bại cho `sectionFromPath`**

Tạo `frontend/src/lib/routes.koc.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { pathFromSection, sectionFromPath } from "./routes";

describe("koc-orders routing", () => {
  it("maps /koc-orders to section koc-orders", () => {
    expect(sectionFromPath("/koc-orders")).toBe("koc-orders");
  });

  it("does not confuse /koc-orders with /orders", () => {
    expect(sectionFromPath("/orders")).toBe("orders");
  });

  it("round-trips section to path", () => {
    expect(pathFromSection("koc-orders")).toBe("/koc-orders");
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npx vitest run src/lib/routes.koc.test.ts`
Expected: FAIL — `"koc-orders"` chưa thuộc `AppSection`; `sectionFromPath("/koc-orders")` trả `"orders"`.

- [ ] **Step 3: Thêm `koc-orders` vào AppSection**

Trong `frontend/src/lib/routes.ts` dòng 1, thêm `"koc-orders"` vào union (sau `"landing-orders"`):

```typescript
export type AppSection = "orders" | "landing-orders" | "koc-orders" | "customers" | "customer-report" | "loyal-customers" | "resale-tracking" | "dashboard" | "ads-report" | "channels" | "kiotviet" | "facebook-ads" | "users" | "products" | "product-categories" | "audit" | "cron-jobs" | "sync" | "zalo-accounts";
```

- [ ] **Step 4: Thêm mapping path**

Trong `SECTION_TO_PATH` (dòng 8-27), thêm sau dòng `"landing-orders": "/landing-orders",`:

```typescript
  "koc-orders": "/koc-orders",
```

- [ ] **Step 5: Xử lý `sectionFromPath`**

Trong `sectionFromPath` (dòng 50-62), thêm NGAY SAU dòng `if (normalized.startsWith("/landing-orders")) return "landing-orders";` (dòng 53):

```typescript
  if (normalized.startsWith("/koc-orders")) return "koc-orders";
```

- [ ] **Step 6: Chạy test, xác nhận PASS**

Run: `cd frontend && npx vitest run src/lib/routes.koc.test.ts`
Expected: PASS — cả 3 test xanh.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/routes.ts frontend/src/lib/routes.koc.test.ts
git commit -m "Add koc-orders section routing"
```

---

## Task 4: Frontend — FilterScope + chế độ endpoint KOC trong use-orders-data

**Files:**
- Modify: `frontend/src/features/orders/use-orders-data.ts:367-373` (FilterScope), `:716-724` (mode ref), `:1211` (endpoint), và export hook ở cuối (~`:2248`)

- [ ] **Step 1: Mở rộng `FilterScope`**

Trong `use-orders-data.ts` dòng 367-373, thêm 3 scope koc:

```typescript
export type FilterScope =
  | "orders"
  | "orders-delivered"
  | "orders-cancelled"
  | "landing-sale"
  | "landing-delivered"
  | "landing-cancelled"
  | "koc"
  | "koc-delivered"
  | "koc-cancelled";
```

- [ ] **Step 2: Thêm `kocModeRef` + `setKocMode`**

Trong hook `useOrdersData` (sau block `setLandingMode` kết thúc ở dòng 724), thêm:

```typescript
  const kocModeRef = useRef<boolean>(false);
  const setKocMode = useCallback((value: boolean) => {
    kocModeRef.current = !!value;
  }, []);
```

- [ ] **Step 3: Mở rộng chọn endpoint**

Sửa dòng 1211 (`const endpoint = isLandingMode ? ...`):

```typescript
      const endpoint = isLandingMode
        ? `/api/orders/landing`
        : kocModeRef.current
          ? `/api/orders/koc`
          : `/api/orders`;
```

> Lưu ý: KOC dùng `isLandingMode = false` nên `buildBody` vẫn gửi `show_orders` + `kiotviet_channel_null` như trang orders thường. `kocModeRef` chỉ đổi endpoint.

- [ ] **Step 4: Export `setKocMode` từ hook**

Trong object return của `useOrdersData` (gần dòng 2248, nơi có `switchFilterScope,`), thêm dòng:

```typescript
    setKocMode,
```

(đặt cạnh `setLandingMode` nếu nó cũng được export; nếu `setLandingMode` đã có trong return thì thêm `setKocMode` ngay dưới nó.)

- [ ] **Step 5: Kiểm tra type-check (không có test riêng cho bước này)**

Run: `cd frontend && npx tsc --noEmit`
Expected: Không có lỗi mới liên quan `FilterScope`/`setKocMode`. (Nếu báo lỗi `setKocMode` không tồn tại ở chỗ App.tsx dùng — sẽ được nối ở Task 5; chấp nhận lỗi tạm thời này nếu chạy tsc trước Task 5, hoặc chạy tsc sau Task 5.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/orders/use-orders-data.ts
git commit -m "Add koc filter scopes and koc endpoint mode to orders data hook"
```

---

## Task 5: Frontend — orchestration trong App.tsx

**Files:**
- Modify: `frontend/src/App.tsx` — dòng 34-39 (`getOrderTabScope`), 543-587 (`loadSectionData`), 588-621 (reset koc mode ở các nhánh khác), 716/1462/1468 (allowedSections), 757-760 (page title), 1267-1278 (`navigateToSection`), 1280-1318 (`handleLandingTabChange`), 1326-1330 (`navigateBackToOrderList`), 1488 + 1497-1622 (render block).

- [ ] **Step 1: Mở rộng `getOrderTabScope`**

Sửa chữ ký + thân hàm (dòng 34-39):

```typescript
function getOrderTabScope(section: "orders" | "landing-orders" | "koc-orders", tab: LandingTabKey): FilterScope {
  if (section === "landing-orders") {
    if (tab === "delivered") return "landing-delivered";
    if (tab === "cancelled") return "landing-cancelled";
    return "landing-sale";
  }
  if (section === "koc-orders") {
    if (tab === "delivered") return "koc-delivered";
    if (tab === "cancelled") return "koc-cancelled";
    return "koc";
  }
```

(giữ nguyên phần còn lại của hàm cho section `"orders"`.)

- [ ] **Step 2: Reset `setKocMode(false)` ở nhánh orders và landing**

Trong `loadSectionData`, nhánh `targetSection === "orders"`: thêm `orders.setKocMode(false);` ngay sau `orders.setLandingMode(false);` (dòng 547).

Nhánh `targetSection === "landing-orders"`: thêm `orders.setKocMode(false);` ngay sau `orders.setLandingMode(true);` (dòng 589).

- [ ] **Step 3: Thêm nhánh `koc-orders` trong `loadSectionData`**

Ngay SAU khi kết thúc nhánh `if (targetSection === "landing-orders") { ... return; }` (dòng 621), chèn nhánh mới — sao y nhánh orders nhưng bật koc mode:

```typescript
    if (targetSection === "koc-orders") {
      const initialTab = parseLandingTab(window.location.search);
      setLandingTab(initialTab);
      orders.setLandingMode(false);
      orders.setKocMode(true);
      const loaded = orders.switchFilterScope(
        getOrderTabScope("koc-orders", initialTab),
        getOrderTabFallbackDefaults(initialTab)
      );
      orders.setKiotvietChannelNull("");
      orders.setShowOrders("true");
      await Promise.all([
        orders.loadOrders(
          {
            kiotvietChannelNull: "",
            showOrders: "true",
            dateFilter: loaded.dateFilter,
            status: loaded.status,
            channel: loaded.channel,
            product: loaded.product,
            staff: loaded.staff,
            deliveryBusinessStatus: loaded.deliveryBusinessStatus,
            salesStatus: loaded.salesStatus,
            kiotvietChannel: loaded.kiotvietChannel,
            branchId: loaded.branchId,
            invoiceStatus: loaded.invoiceStatus,
            hasShippingCode: loaded.hasShippingCode,
            hasInvoiceCode: loaded.hasInvoiceCode,
            isDeliveryInvoicePrinted: loaded.isDeliveryInvoicePrinted,
          },
          { force: true }
        ),
        orders.loadProducts(),
        orders.loadKiotVietOptions(),
        loadChannels(),
        loadAllChannels(),
        loadStaffUsers(),
        loadZaloAccounts(),
      ]);
      orders.loadAddressCatalog().catch(() => null);
      return;
    }
```

- [ ] **Step 4: Page title cho koc-orders**

Sửa block page title (dòng 757-760), thêm nhánh koc-orders sau landing-orders:

```typescript
      : section === "orders"
        ? t("page.title.orders")
        : section === "landing-orders"
          ? t("page.title.landingOrders")
          : section === "koc-orders"
            ? t("page.title.kocOrders")
```

(điều chỉnh thụt lề các nhánh phía sau cho khớp — chỉ thêm 1 nhánh ternary; đảm bảo nhánh `customers` kế tiếp vẫn nối đúng.)

- [ ] **Step 5: `navigateToSection` — bao gồm koc-orders**

Sửa 2 điều kiện ở dòng 1272 và 1275:

```typescript
    if (nextSection === "orders" || nextSection === "landing-orders" || nextSection === "koc-orders") {
      setOrdersView({ type: "list" });
    }
    if (nextSection === "orders" || nextSection === "landing-orders" || nextSection === "koc-orders") {
      setLandingTab("orders");
    }
```

- [ ] **Step 6: `handleLandingTabChange` — hỗ trợ koc-orders**

Sửa dòng 1290-1291:

```typescript
    const orderSection: "orders" | "landing-orders" | "koc-orders" =
      sectionRef.current === "landing-orders"
        ? "landing-orders"
        : sectionRef.current === "koc-orders"
          ? "koc-orders"
          : "orders";
    const showOrdersValue = orderSection === "landing-orders" ? "" : "true";
```

- [ ] **Step 7: `navigateBackToOrderList` — path đúng theo section**

Sửa dòng 1327:

```typescript
    const path = pathFromSection(
      section === "landing-orders" ? "landing-orders" : section === "koc-orders" ? "koc-orders" : "orders"
    );
```

- [ ] **Step 8: allowedSections mặc định**

Tại 3 vị trí có `["orders", "landing-orders", "customers"]` (dòng 716, 1462, 1468), thêm `"koc-orders"`:

```typescript
["orders", "landing-orders", "koc-orders", "customers"]
```

- [ ] **Step 9: Nới rộng render block để KOC tái dùng UI orders**

Sửa className `<main>` ở dòng 1488 — thêm koc-orders vào điều kiện layout:

```typescript
          <main className={`min-h-0 min-w-0 flex-1 ${section === "orders" || section === "landing-orders" || section === "koc-orders" ? "overflow-hidden flex flex-col p-1.5 sm:p-2" : "overflow-y-auto p-3 sm:p-4 space-y-4"}`}>
```

Sửa wrapper render orders ở dòng 1497 từ `{section === "orders" && (` thành:

```typescript
              {(section === "orders" || section === "koc-orders") && (
```

Và 3 điều kiện con bên trong block đó (dòng 1499, 1504, 1589) đổi `section === "orders"` thành `(section === "orders" || section === "koc-orders")`:

```typescript
                  {(section === "orders" || section === "koc-orders") && ordersView.type === "list" && landingTab === "blocklist" && (
```
```typescript
                  {(section === "orders" || section === "koc-orders") && ordersView.type === "list" && landingTab !== "blocklist" && (
```
```typescript
                  {(section === "orders" || section === "koc-orders") && ordersView.type === "detail" && (() => {
```

> KOC tái dùng nguyên block orders (tabs + filter + cột), không nhân bản code. Chi tiết đơn vẫn dùng route `/orders/:id` chung — chấp nhận được vì trang chi tiết không phụ thuộc section.

- [ ] **Step 10: Type-check toàn bộ frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — không còn lỗi (kể cả `setKocMode` từ Task 4 đã được dùng đúng).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "Wire koc-orders section through App orchestration and render branches"
```

---

## Task 6: Frontend — Sidebar nav item + i18n

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx` (import icon + nhóm `sale` dòng 82-85)
- Modify: `frontend/src/i18n/messages.ts` (vi: ~541/562; en: ~1199/1220)

- [ ] **Step 1: Thêm icon import trong Sidebar**

Trong `frontend/src/components/Sidebar.tsx`, thêm `Sparkles` vào dòng import từ `lucide-react` (cùng nơi `RadioTower` được import).

- [ ] **Step 2: Thêm nav item vào nhóm `sale`**

Sửa nhóm `sale` (dòng 82-85), thêm item koc-orders dưới landing-orders:

```typescript
    items: [
      { key: "orders", labelKey: "topnav.tabs.orders", icon: ShoppingBag },
      { key: "landing-orders", labelKey: "topnav.tabs.landingOrders", icon: RadioTower },
      { key: "koc-orders", labelKey: "topnav.tabs.kocOrders", icon: Sparkles },
    ],
```

- [ ] **Step 3: Thêm i18n tiếng Việt**

Trong `frontend/src/i18n/messages.ts`, sau dòng 541 (`"topnav.tabs.landingOrders": "Đơn hàng Landing Page",`) thêm:

```typescript
    "topnav.tabs.kocOrders": "Đơn hàng KOC/KOL",
```

Sau dòng 562 (`"page.title.landingOrders": "Đơn Landing Page",`) thêm:

```typescript
    "page.title.kocOrders": "Đơn hàng KOC/KOL",
```

- [ ] **Step 4: Thêm i18n tiếng Anh**

Sau dòng 1199 (`"topnav.tabs.landingOrders": "Landing Page Orders",`) thêm:

```typescript
    "topnav.tabs.kocOrders": "KOC/KOL Orders",
```

Sau dòng 1220 (`"page.title.landingOrders": "Landing Page Orders",`) thêm:

```typescript
    "page.title.kocOrders": "KOC/KOL Orders",
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/i18n/messages.ts
git commit -m "Add KOC/KOL orders sidebar entry and i18n labels"
```

---

## Task 7: Verify end-to-end + full test pass

**Files:** (không sửa file — chỉ chạy kiểm tra)

- [ ] **Step 1: Backend test KOC pass**

Run: lệnh backend test ở mục **Test Commands**.
Expected: PASS toàn bộ `apps.orders.tests.test_koc_orders_api`.

- [ ] **Step 2: Frontend unit test + type-check**

Run:
```bash
cd frontend && npx vitest run src/lib/routes.koc.test.ts && npx tsc --noEmit
```
Expected: test xanh, không lỗi type.

- [ ] **Step 3: Build frontend (xác nhận không vỡ build)**

Run: `cd frontend && npm run build`
Expected: build thành công.

- [ ] **Step 4: Rà soát các nhánh `landing-orders` còn sót (rủi ro chính)**

Run: `cd frontend && grep -n '=== "landing-orders"' src/App.tsx`
Kiểm tra từng kết quả: nhánh nào quyết định UI/luồng danh sách đơn và KOC cần hành xử như orders thì phải đã được xử lý ở Task 5. Ghi chú nếu phát hiện nhánh sót và sửa.

- [ ] **Step 5: Commit (nếu có sửa ở Step 4)**

```bash
git add -A && git commit -m "Fix remaining landing-orders branch for koc-orders parity"
```

---

## Self-Review Notes

- **Spec coverage:** Backend setting + filter (Task 1), endpoint (Task 2), routing (Task 3), FilterScope/endpoint mode (Task 4), App orchestration + render (Task 5), Sidebar + i18n (Task 6), tests (Task 1/3/7). Tất cả mục trong spec đều có task.
- **No badge counts:** view KOC trả `{results, count}`, không `sales_status_counts` — đúng spec.
- **Scope khóa server:** `channel_ids=[KOC_CHANNEL_ID]` + `_apply_user_channel_scope` (AND theo code) — đúng spec mục 4.2/4.3.
- **Type consistency:** `setKocMode` (Task 4 định nghĩa + export) dùng ở App.tsx (Task 5); `FilterScope` koc/koc-delivered/koc-cancelled khớp giữa Task 4 và `getOrderTabScope` Task 5.
