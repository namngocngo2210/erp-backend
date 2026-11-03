# Trang "Vận đơn" (mục Kho) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm trang Vận đơn trong module Kho, hiển thị đơn đã có mã vận đơn + sale "đã lên đơn", lọc theo ngày tạo / trạng thái vận chuyển thực / trạng thái hoá đơn / trạng thái vận chuyển, cho cập nhật inline trạng thái vận chuyển thực.

**Architecture:** Backend thêm 1 endpoint mỏng `POST /api/orders/shipments` (mirror `list_koc_orders`) khóa cứng `has_shipping_code=yes` + `sales_status=["created_order"]`; tái dùng `OrderRepository.list_orders` (không đụng query/migration). Frontend là trang Kho độc lập, đi qua `routed-pages.tsx` (như StockPage), tái dùng `OrdersTable` + bộ lọc gọn riêng; gọi PATCH `/api/orders/:id` sẵn có cho cập nhật inline.

**Tech Stack:** Django REST + DRF, pytest; React + TS + react-router, shadcn/ui, react-select.

**Spec:** `docs/superpowers/specs/2026-06-16-shipments-page-design.md`

---

## File Structure

**Backend (create/modify):**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` — thêm `list_shipment_orders`.
- Modify: `backend/apps/orders/views.py` — import + export `list_shipment_orders`.
- Modify: `backend/apps/orders/urls.py` — route `shipments`.
- Modify: `backend/apps/users/models.py` — thêm `"shipments"` vào `VALID_SECTIONS`.
- Modify: `backend/apps/users/rbac_catalog.py` — thêm `"shipments"` vào `PERMISSION_CATALOG`.
- Test: `backend/apps/orders/tests/test_shipment_orders_api.py`.

**Frontend (create/modify):**
- Modify: `frontend/src/lib/routes.ts` — `AppSection` union + `SECTION_TO_PATH`.
- Modify: `frontend/src/app/modules.ts` — thêm section `shipments` vào ĐẦU `inventory.sections`.
- Modify: `frontend/src/app/routed-pages.tsx` — lazy import + route export.
- Modify: `frontend/src/app/routes.tsx` — route `inventory/shipments`.
- Modify: `frontend/src/i18n/messages/sidebar.ts` (+ `orders.ts` nếu cần) — label.
- Create: `frontend/src/features/inventory/pages/ShipmentsPage.tsx` — trang.
- Create: `frontend/src/features/inventory/shipments/use-shipments.ts` — hook fetch + patch.
- Create: `frontend/src/features/inventory/shipments/ShipmentsFilterBar.tsx` — bộ lọc gọn.
- Create: `frontend/src/features/inventory/shipments/shipment-columns.ts` — preset cột rút gọn.

---

## SPRINT 1 — Backend endpoint + RBAC

### Task 1: Đăng ký section quyền `shipments`

**Files:**
- Modify: `backend/apps/users/models.py:6-31`
- Modify: `backend/apps/users/rbac_catalog.py:35-39`

- [ ] **Step 1: Thêm section vào VALID_SECTIONS**

Trong `backend/apps/users/models.py`, thêm `"shipments"` ngay sau `"returns"`:

```python
    "stock",
    "stock-documents",
    "returns",
    "shipments",
    "users",
```

- [ ] **Step 2: Thêm quyền vào PERMISSION_CATALOG**

Trong `backend/apps/users/rbac_catalog.py`, mục `# Kho`, thêm dòng sau `"returns": (...)`:

```python
    "returns": ("view", "create", "edit", "approve"),
    "shipments": ("view", "edit"),
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/users/models.py backend/apps/users/rbac_catalog.py
git commit -m "feat(rbac): add shipments section permission"
```

### Task 2: Endpoint `list_shipment_orders` (TDD)

**Files:**
- Test: `backend/apps/orders/tests/test_shipment_orders_api.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (thêm hàm cuối file)
- Modify: `backend/apps/orders/views.py:42-46,101-105`
- Modify: `backend/apps/orders/urls.py:44-46,63-66`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_shipment_orders_api.py`. Bám pattern các test order hiện có (xem `test_order_crm_api.py` để biết cách tạo user có quyền + auth client; tái dùng helper/fixtures sẵn có trong package test). Nội dung kiểm:

```python
import pytest
from django.urls import reverse

from apps.orders.models import Order, OrderStatus, OrderAssessment


@pytest.fixture
def shipment_url():
    return reverse("orders-shipments-list")


def _make_order(**kwargs):
    defaults = dict(status=OrderStatus.PENDING_PROCESSING)
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


@pytest.mark.django_db
def test_only_returns_orders_with_shipping_code_and_created_order_sales_status(
    auth_client_with_section, shipment_url
):
    client = auth_client_with_section("shipments")
    # khớp: có shipping_code + sales_status created_order
    matched = _make_order(shipping_code="GHTK1")
    OrderAssessment.objects.create(order=matched, sales_status_code="created_order")
    # loại: không có shipping_code
    no_code = _make_order(shipping_code="")
    OrderAssessment.objects.create(order=no_code, sales_status_code="created_order")
    # loại: sai sales_status
    wrong_sale = _make_order(shipping_code="GHTK2")
    OrderAssessment.objects.create(order=wrong_sale, sales_status_code="closed")

    resp = client.post(shipment_url, {"limit": 50, "offset": 0}, format="json")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.data["results"]}
    assert matched.id in ids
    assert no_code.id not in ids
    assert wrong_sale.id not in ids


@pytest.mark.django_db
def test_client_cannot_override_locked_filters(auth_client_with_section, shipment_url):
    client = auth_client_with_section("shipments")
    o = _make_order(shipping_code="GHTK9")
    OrderAssessment.objects.create(order=o, sales_status_code="created_order")
    # client cố bỏ điều kiện -> vẫn bị ép
    resp = client.post(
        shipment_url,
        {"limit": 50, "offset": 0, "has_shipping_code": "no", "sales_status": ["closed"]},
        format="json",
    )
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.data["results"]}
    assert o.id in ids  # vẫn trả vì server ép created_order + has_shipping_code=yes


@pytest.mark.django_db
def test_requires_shipments_permission(auth_client_with_section, shipment_url):
    client = auth_client_with_section("orders")  # không có shipments
    resp = client.post(shipment_url, {"limit": 50, "offset": 0}, format="json")
    assert resp.status_code == 403
```

LƯU Ý: bỏ dòng rác `OrderAssessment.objects.create(no_code and None...)` — đó là nhầm. Test thực tế chỉ cần 3 đơn (matched/no_code/wrong_sale). Nếu fixture `auth_client_with_section` chưa tồn tại, tạo trong `conftest.py` của package test bằng cách: tạo user, gán role/permission `shipments:view` (theo cơ chế RBAC hiện có — đọc `apps/users` để dùng đúng API tạo quyền), trả về `APIClient` đã `force_authenticate`.

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Chạy theo memory `running-backend-tests` (ephemeral docker run trên net `desi-order_default`). Kỳ vọng: lỗi `NoReverseMatch` cho `orders-shipments-list`.

- [ ] **Step 3: Thêm view `list_shipment_orders`**

Cuối `order_list_view.py`, thêm (mirror `list_koc_orders`, đổi section + khóa cứng 2 filter):

```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("shipments")])
@never_cache
def list_shipment_orders(request):
    """Trang Vận đơn (Kho) — khóa cứng đơn có mã vận đơn + sale đã lên đơn."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    sort_value = _parse_sort(request, default="newest")
    filters = {
        "q": source.get("q", ""),
        "status": _parse_multi_query_values(request, "status"),
        "invoice_status": _parse_multi_query_values(request, "invoice_status"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # Khóa cứng — không phụ thuộc client gửi gì.
        "has_shipping_code": "yes",
        "sales_status": ["created_order"],
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    filters, scope_empty = _apply_user_channel_scope(filters, request.user, "shipments")
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
        logger.exception("Failed to query/serialize shipment orders, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách vận đơn. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)
```

LƯU Ý scope: `_apply_user_channel_scope(filters, request.user, "shipments")` — truyền đúng section `"shipments"` (theo memory `rbac-scope-by-section-gotcha`, không để mặc định gây scope OWN sai).

- [ ] **Step 4: Export view + route**

Trong `backend/apps/orders/views.py`: thêm `list_shipment_orders` vào khối import từ `.transport...order_list_view` (cạnh `list_koc_orders`) và vào `__all__`.

Trong `backend/apps/orders/urls.py`: thêm `list_shipment_orders` vào import (dòng ~44) và route sau dòng `koc`:

```python
    path("koc", list_koc_orders, name="orders-koc-list"),
    path("shipments", list_shipment_orders, name="orders-shipments-list"),
```

- [ ] **Step 5: Chạy test — kỳ vọng PASS**

Chạy lại file test. Kỳ vọng 3 test PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/ 
git commit -m "feat(orders): add shipments list endpoint"
```

---

## SPRINT 2 — Frontend: routing, menu, quyền

### Task 3: Khai báo section `shipments` (routes + module)

**Files:**
- Modify: `frontend/src/lib/routes.ts:3-50`
- Modify: `frontend/src/app/modules.ts:79-90`
- Modify: `frontend/src/i18n/messages/sidebar.ts`

- [ ] **Step 1: AppSection + path mapping**

Trong `frontend/src/lib/routes.ts`: thêm `| "shipments"` vào union `AppSection` (cạnh các section inventory), và vào `SECTION_TO_PATH`:

```typescript
  shipments: "/inventory/shipments",
  warehouses: "/inventory/warehouses",
```

- [ ] **Step 2: Thêm section vào ĐẦU inventory.sections**

Trong `frontend/src/app/modules.ts`, module `inventory`, thêm vào đầu mảng `sections` (import `Truck` từ `lucide-react` ở đầu file cùng các icon khác):

```typescript
    sections: [
      { key: "shipments", labelKey: "topnav.tabs.shipments", icon: Truck },
      { key: "stock", labelKey: "topnav.tabs.stock", icon: Boxes },
```

- [ ] **Step 3: i18n label**

Thêm key `"topnav.tabs.shipments": "Vận đơn"` vào file messages chứa các key `topnav.tabs.*` (tìm `topnav.tabs.stock` để xác định file; thường `i18n/messages/sidebar.ts` hoặc `topnav.ts`). Thêm vào cả locale `vi` và `en` nếu file tách locale.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/i18n/
git commit -m "feat(shipments): register shipments section + menu"
```

### Task 4: Route + lazy page rỗng (smoke)

**Files:**
- Modify: `frontend/src/app/routed-pages.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Create: `frontend/src/features/inventory/pages/ShipmentsPage.tsx` (placeholder tạm)

- [ ] **Step 1: Page placeholder**

Tạo `ShipmentsPage.tsx` tạm để verify routing trước:

```tsx
export default function ShipmentsPage() {
  return <div className="p-4">Vận đơn</div>;
}
```

- [ ] **Step 2: Lazy import + route export trong routed-pages.tsx**

Thêm lazy import cạnh `StockPage`:

```tsx
const ShipmentsPage = lazy(() => import("@/features/inventory/pages/ShipmentsPage"));
```

Tìm cách `StockPage`/`ReturnsPage` được export thành route component trong file này (cuối file có các wrapper `export function XxxRoute()` hoặc tương tự) và thêm `ShipmentsRoute` theo đúng khuôn — đọc đoạn export StockPage và mirror y hệt (props từ `useRootNav()` nếu cần; ShipmentsPage placeholder chưa cần props).

- [ ] **Step 3: Route trong routes.tsx**

Trong `frontend/src/app/routes.tsx`, thêm route trước nhóm inventory hiện có (đặt đầu nhóm Kho), mirror route `inventory/stock`:

```tsx
{
  path: "inventory/shipments",
  element: (
    <Guard section="shipments">
      <ShipmentsRoute />
    </Guard>
  ),
},
```

(đọc route `inventory/stock` để dùng đúng tên `Guard`/wrapper thực tế trong file).

- [ ] **Step 4: Verify routing**

Chạy `npm --prefix frontend run build` (hoặc `tsc --noEmit`) — kỳ vọng pass. Vào `/inventory/shipments` thấy chữ "Vận đơn" + menu Kho có mục "Vận đơn" ở đầu (cần user có quyền `shipments:view`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/ frontend/src/features/inventory/pages/ShipmentsPage.tsx
git commit -m "feat(shipments): route + placeholder page"
```

---

## SPRINT 3 — Frontend: dữ liệu, bảng, bộ lọc

### Task 5: Hook `use-shipments.ts` (fetch + patch)

**Files:**
- Create: `frontend/src/features/inventory/shipments/use-shipments.ts`

- [ ] **Step 1: Viết hook**

Hook nhận `fetchJson` (lấy từ context app — xem cách `use-orders-data` nhận `fetchJson`; tái dùng cùng provider). State: `rows`, `count`, `loading`, `page`, `pageSize`, và filter state (`dateFilter`, `status`, `invoiceStatus`, `deliveryBusinessStatus`). Hàm `load()` POST `/api/orders/shipments` với body chỉ gồm: `limit`, `offset`, `sort:"newest"`, và `date_from/date_to` (từ dateFilter, dùng helper convert sẵn có của date-filter), `status` (lọc bỏ "6" như orders), `invoice_status`, `delivery_business_status` khi có. Hàm `patchOrder(id, payload)` PATCH `/api/orders/:id` (mirror `updateOrderInline` trong use-orders-data: method PATCH, body JSON) rồi cập nhật row trong state tại chỗ.

Tái dùng kiểu `OrderRow` từ `@/features/orders/order-types`. KHÔNG nhân bản toàn bộ use-orders-data — chỉ phần tối thiểu cho list + patch.

- [ ] **Step 2: tsc check + commit**

```bash
npm --prefix frontend run -s tsc -- --noEmit || npx --prefix frontend tsc --noEmit
git add frontend/src/features/inventory/shipments/use-shipments.ts
git commit -m "feat(shipments): data hook (list + inline patch)"
```

### Task 6: Preset cột rút gọn `shipment-columns.ts`

**Files:**
- Create: `frontend/src/features/inventory/shipments/shipment-columns.ts`

- [ ] **Step 1: Định nghĩa cột**

Xuất mảng `OrderColumnKey[]` đúng tên key có trong `ORDER_COLUMN_OPTIONS` (`order-types.ts:262-318` — đọc để lấy đúng key). Bộ rút gọn theo spec:

```typescript
import { type OrderColumnKey } from "@/features/orders/order-types";

export const SHIPMENT_COLUMNS: OrderColumnKey[] = [
  "shipping_code",
  "shipping_provider",
  "invoice_code",
  // khách hàng, sản phẩm — dùng đúng key thực tế trong ORDER_COLUMN_OPTIONS
  "status",
  "invoice_status",
  "delivery_business_status",
  "created_at",
];
```

Đối chiếu từng key với `ORDER_COLUMN_OPTIONS`; thay key khách hàng/sản phẩm bằng key thật (vd `customer_name`/`product`...). Bỏ key không tồn tại.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/features/inventory/shipments/shipment-columns.ts
git commit -m "feat(shipments): shipment column preset"
```

### Task 7: Bộ lọc gọn `ShipmentsFilterBar.tsx`

**Files:**
- Create: `frontend/src/features/inventory/shipments/ShipmentsFilterBar.tsx`

- [ ] **Step 1: Viết filter bar**

Component nhận props: `dateFilter`+`onDateFilterChange` (reuse `DateFilterValue` + component `date-filter` ở `@/components/ui/date-filter`), `status`+`onStatusChange`, `invoiceStatus`+`onInvoiceStatusChange`, `deliveryBusinessStatus`+`onDeliveryBusinessStatusChange`. Options:
- `statusOptions`: 14 mục `status.{i}` (mirror `OrdersPage.tsx:586-593`).
- `invoiceStatusOptions`: `[1,2,3,5]` → `invoiceStatus.{v}` (mirror `OrdersPage.tsx:598-605`).
- `deliveryBusinessStatusOptions`: lấy từ catalog API (`/api/orders/crm-catalogs` → `delivery_business_status_options`) HOẶC hard-code 6 mục đồng bộ với `crm_catalog.py` (warehouse_processing/dispatched/received/not_received/stalled_complaint/post_office_lost). Ưu tiên catalog API để không lệch.

Dùng `react-select` multi cho 3 select trạng thái (mirror cách `OrdersFilterPanel` render select). Chỉ HIỂN THỊ đúng 4 nhóm lọc này — không có filter khác.

- [ ] **Step 2: tsc + commit**

```bash
git add frontend/src/features/inventory/shipments/ShipmentsFilterBar.tsx
git commit -m "feat(shipments): slim filter bar"
```

### Task 8: Lắp ráp `ShipmentsPage.tsx` (thay placeholder)

**Files:**
- Modify: `frontend/src/features/inventory/pages/ShipmentsPage.tsx`

- [ ] **Step 1: Lắp page**

Thay placeholder: dùng `use-shipments`, render `ShipmentsFilterBar` + `OrdersTable` + `OrdersPagination`. Với `OrdersTable`, đọc `Props` của nó (`components/OrdersTable.tsx`) để truyền đủ prop bắt buộc:
- `rows`, `visibleColumns={SHIPMENT_COLUMNS}`, `lockedColumns={[]}`.
- `onPatch={patchOrder}` (cho sửa inline cột Trạng thái vận chuyển thực).
- Các prop danh mục (`kiotvietOptions`, `addressCatalog`, `channels`, `channelStaffByCode`, `products`) — truyền giá trị rỗng/tối thiểu nếu bảng cho phép; nếu prop là bắt buộc và bảng cần để render cột nào đó ngoài preset, hoặc (a) cung cấp từ context app, hoặc (b) vì preset cột rút gọn không cần chúng, truyền mảng rỗng/`{}` an toàn. Mục tiêu: bảng render được bộ cột rút gọn + sửa inline `delivery_business_status`.
- `filterKey`: JSON.stringify filter hiện tại để reset selection khi đổi lọc.
- Tắt các tính năng không thuộc phạm vi (tạo đơn/import/landing tabs) bằng cách KHÔNG truyền các callback đó / để cờ tương ứng false.

- [ ] **Step 2: Verify build + chạy thử**

```bash
npm --prefix frontend run build
```
Kỳ vọng build pass. Mở `/inventory/shipments`: thấy bảng chỉ đơn có mã vận đơn + sale đã lên đơn; lọc 4 nhóm hoạt động; sửa inline cột "Trạng thái vận chuyển thực" gọi PATCH thành công và cập nhật tại chỗ.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/inventory/pages/ShipmentsPage.tsx
git commit -m "feat(shipments): assemble shipments page"
```

---

## SPRINT 4 — Hoàn thiện & kiểm thử

### Task 9: Kiểm thử end-to-end + dọn dẹp

- [ ] **Step 1:** Verify quyền: user KHÔNG có `shipments:view` không thấy menu + truy cập route bị chặn (Guard); admin cấp quyền `shipments` rồi thấy.
- [ ] **Step 2:** Verify khóa cứng: bảng không bao giờ hiện đơn thiếu mã vận đơn hoặc sale ≠ created_order, kể cả khi đổi các filter còn lại.
- [ ] **Step 3:** Verify phân trang + sort hoạt động.
- [ ] **Step 4:** Chạy lại test backend (`test_shipment_orders_api.py`) + `npm run build` FE — đều pass.
- [ ] **Step 5:** Cập nhật spec: đánh dấu trạng thái "Đã triển khai" + note việc đã/chưa làm.
- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-06-16-shipments-page-design.md
git commit -m "docs(shipments): mark implemented"
```

---

## Rủi ro / lưu ý khi thực hiện

- **RBAC scope:** luôn truyền section `"shipments"` cho `_apply_user_channel_scope` (memory `rbac-scope-by-section-gotcha`).
- **Sync permission catalog:** sau khi thêm section, chạy lệnh sync catalog quyền (đọc `apps/users` để biết management command / hàm sync) trên môi trường chạy, nếu không endpoint/menu sẽ bị chặn.
- **Backend test:** chạy theo memory `running-backend-tests` (ephemeral docker, prod image thiếu tests/ruff).
- **OrdersTable prop surface:** đọc `Props` thực tế trước khi lắp (Task 8) — đây là rủi ro lớn nhất; nếu bảng đòi prop nặng, cân nhắc truyền tối thiểu vì bộ cột rút gọn không cần dữ liệu danh mục.
- **Baseline tests:** một số test FE/BE đã đỏ sẵn trên master (memory `known-failing-baseline-tests`) — không coi là regression.
