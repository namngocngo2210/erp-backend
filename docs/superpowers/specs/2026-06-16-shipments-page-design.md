# Thiết kế: Trang "Vận đơn" (mục Kho)

- **Ngày:** 2026-06-16
- **Trạng thái:** ✅ Đã triển khai (branch `feature/shipments-page`) — 3/3 test backend pass, FE build pass

## 0. Ghi chú triển khai (2026-06-16)

**Đã làm:** endpoint `POST /api/orders/shipments` (khóa cứng `has_shipping_code=yes` +
`sales_status=[created_order]`) + 3 test; section quyền `shipments` (view/edit); trang Kho
độc lập `features/inventory/pages/ShipmentsPage.tsx` tái dùng `OrdersTable` + bộ lọc gọn
`ShipmentsFilterBar` (ngày tạo / trạng thái vận chuyển / hoá đơn / vận chuyển thực) + hook
`use-shipments` (list + PATCH inline trạng thái vận chuyển thực).

**Khác thiết kế / cần biết:**
- Cột **"Nhà vận chuyển" (`shipping_provider`) bị bỏ** — `OrderColumnKey`/`ORDER_COLUMN_OPTIONS`
  chưa có cột này; chỉ hiển thị mã vận đơn. Muốn có cột nhà vận chuyển phải đăng ký cột mới
  trong `order-types.ts` + `OrdersTable` (việc riêng).
- Cột "Sản phẩm" dùng key `sku` (hiển thị SKU) — hệ thống không có cột product-name riêng.

**Vận hành (BẮT BUỘC trước khi dùng thật):** chạy sync permission catalog để section
`shipments` xuất hiện trong bảng `Permission`, rồi admin cấp quyền `shipments:view`/`edit`
cho user/role cần — nếu không, menu + endpoint sẽ bị chặn.
- **Phạm vi:** 1 trang mới read+edit nhẹ, tái dùng tối đa hạ tầng orders sẵn có

## 1. Mục tiêu

Thêm trang **Vận đơn** trong module **Kho** (inventory), hiển thị các đơn **đã có mã vận đơn**
và **trạng thái sale = "Đã lên đơn"**, phục vụ nhân viên kho theo dõi và cập nhật trạng thái
vận chuyển thực.

## 2. Yêu cầu (đã chốt với product owner)

| # | Quyết định | Giá trị |
|---|-----------|---------|
| 1 | Điều kiện lọc cố định | **Khóa cứng** (server-side): `has_shipping_code = yes` + `sales_status = ["created_order"]` |
| 2 | Tương tác | Cho **cập nhật inline** cột "Trạng thái vận chuyển thực" (`delivery_business_status`) |
| 3 | Bộ cột | **Rút gọn**, tập trung vận đơn |
| 4 | Khoảng ngày lọc | Chỉ **ngày tạo** (`date_from`/`date_to`) |
| 5 | Vị trí menu | **Đầu danh sách** section của nhóm Kho |
| 6 | Quyền | Gate bằng **section quyền mới** của Kho (`shipments`), admin cấp quyền |

Bộ lọc trên trang: khoảng **ngày tạo** · **trạng thái vận chuyển thực** (`delivery_business_status`)
· **trạng thái hoá đơn** (`invoice_status`) · **trạng thái vận chuyển** (`status`).

## 3. Kiến trúc — tái dùng tối đa

Trang = biến thể có-lọc-sẵn của danh sách đơn hàng, bám đúng pattern hiện có
`list_koc_orders` / `list_landing_orders` (đều là `list_orders` + filter khóa cứng).
**Không cần migration DB, không đụng query repository** — mọi filter cần thiết đã được
`OrderRepository._apply_list_filters` hỗ trợ sẵn.

### 3.1 Backend

**Endpoint mới** `POST /api/orders/shipments` (mirror `list_koc_orders`):
- File: `apps/orders/transport/views/order_views_impl/order_list_view.py` — thêm `list_shipment_orders(request)`.
- URL: `apps/orders/urls.py` — route `"shipments"`.
- Khóa cứng server-side sau khi parse filter từ client:
  ```python
  filters["has_shipping_code"] = "yes"
  filters["sales_status"] = ["created_order"]
  ```
  (ghi đè bất kể client gửi gì — đúng nghĩa "khóa cứng").
- Các filter còn lại lấy từ client như `list_orders`: `date_from`, `date_to`, `status`,
  `invoice_status`, `delivery_business_status`, `limit`, `offset`, `sort`, `q`.
- Permission: `@require_section("shipments")`.
- RBAC scope: truyền **đúng section của chính endpoint** khi gọi helper list dùng chung
  (tránh lỗi scope mặc định OWN — theo ghi chú `rbac-scope-by-section-gotcha`).
- Serializer: tái dùng `OrderCRMSerializer` để có sẵn `delivery_business_status_code/label`
  cho cột chỉnh inline. Không thêm serializer mới.
- Cập nhật inline dùng lại endpoint PATCH assessment hiện hành (không tạo mới).

**RBAC — đăng ký section `shipments`:**
- `apps/users/models.py` → thêm `"shipments"` vào `VALID_SECTIONS`.
- `apps/users/rbac_catalog.py` → thêm vào `PERMISSION_CATALOG`:
  `"shipments": ("view", "edit")` (nhóm Kho — `view` xem danh sách, `edit` cập nhật inline
  trạng thái vận chuyển thực; chưa có export ở giai đoạn này).
- Chạy sync permission catalog xuống bảng `Permission` (cơ chế sync sẵn có) — không cần
  migration schema, chỉ seed dữ liệu quyền.

### 3.2 Frontend

- **Module/menu:** `frontend/src/app/modules.ts` → thêm section vào **đầu** mảng `sections`
  của module `inventory`:
  `{ key: "shipments", labelKey: "topnav.tabs.shipments", icon: Truck }`.
  Sidebar tự render theo `APP_MODULES` + lọc theo quyền.
- **Section type:** thêm `"shipments"` vào union `AppSection` và mapping path trong `lib/routes.ts`.
- **Route:** `frontend/src/app/routes.tsx` → route `inventory/shipments` bọc `Guard section="shipments"`;
  lazy import page trong `routed-pages.tsx`.
- **Page:** `features/shipments/pages/ShipmentsPage.tsx` (feature-slice mới `features/shipments`,
  hoặc đặt trong `features/inventory` nếu muốn gom — chọn `features/shipments` cho rạch ròi).
- **Tái dùng `OrdersTable`** với cấu hình:
  - Gọi data qua hook orders sẵn có, trỏ tới endpoint `/api/orders/shipments` (thêm 1 mode
    `"shipments"` vào `use-orders-data.ts`, tương tự mode `koc`/`landing`).
  - **Bộ cột rút gọn:** Mã vận đơn (`shipping_code`) · Nhà vận chuyển (`shipping_provider`)
    · Mã HĐ (`invoice_code`) · Khách hàng · Sản phẩm · Trạng thái vận chuyển (`status`)
    · Trạng thái HĐ (`invoice_status`) · **Trạng thái vận chuyển thực**
    (`delivery_business_status`, chỉnh inline) · Ngày tạo (`created_at`).
  - **Bộ lọc rút gọn** (`OrdersFilterPanel` cấu hình gọn): chỉ hiển thị khoảng **ngày tạo**,
    trạng thái vận chuyển thực, trạng thái hoá đơn, trạng thái vận chuyển. Ẩn các filter khác.
- **i18n:** thêm key `topnav.tabs.shipments` = "Vận đơn" (+ tiêu đề trang, tooltip nếu cần).
- Cột "Trạng thái vận chuyển thực" tái dùng dropdown inline + PATCH assessment đã có.

## 4. Luồng dữ liệu

```
ShipmentsPage
  └─ useOrdersData(mode="shipments")
       └─ POST /api/orders/shipments  { date_from/to, status, invoice_status,
                                         delivery_business_status, limit, offset, sort }
            └─ list_shipment_orders  (force has_shipping_code=yes, sales_status=created_order)
                 └─ OrderRepository.list_orders(filters)   [reuse, no change]
                      └─ OrderCRMSerializer[]   → rows
  └─ inline edit "Trạng thái vận chuyển thực"
       └─ PATCH assessment (endpoint hiện hành)  → cập nhật delivery_business_status_code
```

## 5. Không làm (YAGNI)

- Không tạo bảng/model mới, không migration schema.
- Không sửa `OrderRepository` query logic.
- Không thêm serializer mới.
- Không build filter panel mới từ đầu — cấu hình ẩn/hiện trên `OrdersFilterPanel` hiện có.
- Không thêm cột tùy biến ẩn/hiện (bộ cột cố định rút gọn).

## 6. Kiểm thử

- **Backend:** test `list_shipment_orders` — (a) chỉ trả đơn có `shipping_code` + sale
  `created_order`; (b) client gửi `has_shipping_code=no`/`sales_status=...` bị **ghi đè**
  (khóa cứng); (c) filter date/status/invoice_status/delivery_business_status hoạt động;
  (d) chặn user không có quyền `shipments:view`.
- **Frontend:** smoke test trang render bảng + bộ lọc rút gọn; chỉnh inline gọi PATCH đúng.

## 7. Rủi ro / lưu ý

- RBAC scope mặc định OWN cho section chưa cấp — phải truyền đúng section `shipments` khi
  gọi helper list dùng chung (xem `rbac-scope-by-section-gotcha`).
- Phải chạy sync permission catalog sau khi thêm section, nếu không UI/endpoint sẽ chặn hết.
- Đảm bảo phân trang + sort hoạt động qua mode mới (tái dùng `OrdersPagination`).
