# Thiết kế: Trang Đơn hàng KOC/KOL

- **Ngày:** 2026-05-27
- **Branch:** `koc-kol-orders-page-20260527`
- **Trạng thái:** Đã chốt thiết kế, chờ implement

## 1. Mục tiêu

Thêm một trang "Đơn hàng KOC/KOL" hoạt động **giống hệt trang Đơn hàng thường**
(cùng tabs, filter, cột, luồng thao tác), nhưng dữ liệu **luôn bị giới hạn ở kênh
bán hàng "hàng mẫu koc"** — kênh có `Channel.id = 15` (đã xác nhận tồn tại trên DB).

Khác biệt duy nhất so với trang Đơn hàng thường: scope kênh bị khóa cứng ở server.

## 2. Bối cảnh codebase

- Hai trang đơn hàng hiện tại (`/orders`, `/landing-orders`) **dùng chung component**
  `frontend/src/pages/OrdersPage.tsx`. Phân biệt qua `AppSection` → `FilterScope` →
  chọn endpoint.
  - `/orders` → `POST /api/orders` (`list_orders`)
  - `/landing-orders` → `POST /api/orders/landing` (`list_landing_orders`)
- Đơn hàng liên kết kênh qua `Order.channel` (FK tới `channels_core.Channel`, có
  `id`, `code`, `name`). Kênh KiotViet (`Order.kiotviet_channel`) là khái niệm khác,
  KHÔNG dùng ở đây.
- Filter theo kênh đã có sẵn: tham số `channel` lọc theo `channel__code`
  (`backend/apps/orders/repositories.py:220-225`).
- Quyền xem theo kênh `_apply_user_channel_scope`
  (`backend/apps/orders/transport/views/order_views_impl/_view_helpers.py:70`)
  giao (AND) theo `channel_code`, áp dụng ở mọi endpoint danh sách.

## 3. Hướng triển khai (Hướng A)

Mirror đúng pattern trang Landing: endpoint backend riêng khóa scope ở server +
section frontend mới. Không tổng quát hóa (YAGNI).

## 4. Thay đổi Backend

### 4.1. Hằng số kênh
Thêm `KOC_CHANNEL_ID = 15` (đặt trong settings hoặc constants của app `orders`) để
tránh hardcode rải rác.

### 4.2. Hỗ trợ lọc theo `channel_id` trong repository
Trong `_apply_list_filters` (`backend/apps/orders/repositories.py`), thêm nhánh lọc
theo danh sách channel id:

```python
channel_ids = filters.get("channel_ids")
if channel_ids:
    queryset = queryset.filter(channel_id__in=channel_ids)
```

Lọc theo `channel_id` (neo đúng `id=15`), độc lập với filter `channel` theo code, và
**AND** với `_apply_user_channel_scope` (theo code). Người dùng không có quyền xem
kênh koc → kết quả rỗng (đúng hành vi mong muốn).

### 4.3. View mới `list_koc_orders`
Thêm vào `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` —
bản sao mỏng của `list_orders`:
- Giữ nguyên bộ filter, sort mặc định `newest` (giống trang orders thường).
- Thêm `filters["channel_ids"] = [settings.KOC_CHANNEL_ID]`.
- Vẫn gọi `_apply_user_channel_scope`.
- Trả về `{results, count}` (KHÔNG có `sales_status_counts` — không cần badge counts
  kiểu Landing).

Export view qua `transport/views/__init__` như các view khác.

### 4.4. URL
Thêm vào `backend/apps/orders/urls.py`:
```python
path("koc", list_koc_orders, name="orders-koc-list"),
```

## 5. Thay đổi Frontend

### 5.1. Routing — `frontend/src/lib/routes.ts`
- Thêm `"koc-orders"` vào union `AppSection`.
- Thêm `"koc-orders": "/koc-orders"` vào `SECTION_TO_PATH`.
- Trong `sectionFromPath`, xử lý `/koc-orders` **trước** match `/orders` để không bị
  match nhầm (giống cách `/landing-orders` đang xử lý).

### 5.2. FilterScope — `frontend/src/features/orders/use-orders-data.ts`
- Mở rộng `FilterScope` thêm `"koc" | "koc-delivered" | "koc-cancelled"`. Mỗi scope có
  key localStorage độc lập → filter trang KOC không lẫn với 2 trang kia.
- Mở rộng logic chọn endpoint (hiện là `isLandingMode ? "/api/orders/landing" :
  "/api/orders"`) thành 3 nhánh: scope koc → `POST /api/orders/koc`.

### 5.3. Orchestration — `frontend/src/App.tsx`
- `getOrderTabScope` nhận thêm `"koc-orders"` → trả `koc` / `koc-delivered` /
  `koc-cancelled` theo tab.
- Mở rộng các nhánh đang viết `section === "landing-orders" ? A : B` để KOC dùng đúng
  UI/luồng của trang orders thường (tabs: đơn hàng / giao thành công / đã hủy /
  blocklist như regular). Gom điều kiện render khung danh sách thành
  `isOrderListSection = section === "orders" || section === "landing-orders" ||
  section === "koc-orders"`.
- Bổ sung `"koc-orders"` vào `allowedSections` mặc định.

### 5.4. Sidebar — `frontend/src/components/Sidebar.tsx`
Thêm mục vào nhóm `sale`, ngay dưới Landing:
```ts
{ key: "koc-orders", labelKey: "topnav.tabs.kocOrders", icon: Sparkles },
```
(icon đề xuất `Sparkles` hoặc `Gift`).

### 5.5. i18n
Thêm khóa ở cả vi/en:
- `topnav.tabs.kocOrders` → "Đơn hàng KOC/KOL"
- `page.title.kocOrders` → tiêu đề trang.

## 6. Import / UX

Trang KOC tái dùng toàn bộ UI của trang orders thường (tabs, filter, cột, nút import
dùng endpoint orders thường như `/api/orders/import-text`). Không thêm UI riêng. Khác
biệt duy nhất: dữ liệu luôn giới hạn kênh 15 ở server.

## 7. Testing

- **Backend:** test `list_koc_orders`
  - chỉ trả đơn `channel_id=15`, loại đơn của kênh khác;
  - tôn trọng `_apply_user_channel_scope` (user không có quyền kênh koc → rỗng);
  - phân trang / count đúng.
- **Frontend:** smoke test route `/koc-orders` render đúng section và gọi đúng
  endpoint `/api/orders/koc`.

## 8. Phạm vi & rủi ro

- **Rủi ro chính:** sót một nhánh `=== "landing-orders"` trong `App.tsx` khiến trang
  KOC hiển thị sai khung. Giảm thiểu: rà soát toàn bộ các nhánh đó khi implement.
- **Không trong phạm vi:** badge counts kiểu Landing; tổng quát hóa channel-scoped
  (Hướng C); thay đổi cơ chế import.
- **Đã xác nhận:** `Channel.id = 15` ("hàng mẫu koc") tồn tại trên DB.
