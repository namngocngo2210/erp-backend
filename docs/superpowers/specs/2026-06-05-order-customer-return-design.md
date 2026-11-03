# Thiết kế: Cột & bộ lọc `customer_return` (Khách trả hàng)

- **Ngày:** 2026-06-05
- **Phạm vi:** Thêm 1 trường boolean `customer_return` vào đơn hàng, hiển thị thành cột + bộ lọc trên 3 trang danh sách: Đơn hàng, Đơn hàng Landing, Đơn hàng KOC.

## 1. Bối cảnh & mục tiêu

Cần đánh dấu mỗi đơn xem **khách có trả lại hàng vì không còn nhu cầu sử dụng** hay không.

Đây **KHÔNG** phải nghiệp vụ "đơn hoàn hàng / thu hồi" (đã có model `OrderRecall` riêng cho KOC với `recall_code`, `return_slip_code`...). `customer_return` chỉ là một cờ boolean đơn giản nằm thẳng trên bảng `orders`, tương tự `is_delivery_invoice_printed` / `show_orders`.

Quyết định đã chốt với người dùng:
- **Sửa giá trị:** vừa tick trực tiếp trên ô của lưới (inline), vừa sửa được trong form chi tiết đơn.
- **Hiển thị cột:** bật mặc định trên cả 3 trang.
- **Nhãn:** "Khách trả hàng" (tooltip phân biệt với đơn hoàn/thu hồi).
- **Lưới:** checkbox tick trực tiếp (vừa xem vừa sửa), không chỉ hiện chữ Có/Không.

## 2. Kiến trúc & pattern bám theo

3 trang dùng chung component `OrdersPage` + `OrdersFilterPanel` + `order-grid-columns`, nên thêm 1 lần là cả 3 trang có. Mọi thay đổi bám đúng pattern boolean sẵn có là `is_delivery_invoice_printed` (đọc/serializer/filter yes-no) và `show_orders`.

## 3. Backend

### 3.1. Model & migration
- `backend/apps/orders/models.py` — class `Order`: thêm
  ```python
  customer_return = models.BooleanField(default=False)
  ```
- Migration mới `0075_order_customer_return.py` (AddField, `default=False`). Đơn cũ tự động = `False`.
- **Không thêm index riêng** — bám theo `is_delivery_invoice_printed` (cũng không có index); filter luôn đi kèm date-range + channel-scope nên index boolean cardinality thấp không đáng.

### 3.2. Serializer đọc
- `OrderSerializer.Meta.fields` (order_core_serializers.py): thêm `"customer_return"`. Áp dụng cho cả 3 list API + GET chi tiết.

### 3.3. Serializer ghi (inline + form chi tiết dùng chung)
- `OrderInlineUpdateSerializer.Meta.fields` (kiotviet_landing_serializers.py): thêm `"customer_return"`.
- Service `OrderService.update_order_inline` đã `setattr` generic theo `validated_data` rồi `save(update_fields=[*data.keys(), ...])`, nên **không cần code lưu mới** — cả tick lưới lẫn sửa form đều đi qua endpoint PATCH `update_order_inline` sẵn có.

### 3.4. Bộ lọc repository
- `OrderRepository._apply_list_filters` (repositories.py): thêm khối, copy nguyên pattern `is_delivery_invoice_printed`:
  ```python
  customer_return = str(filters.get("customer_return") or "").strip()
  if customer_return == "yes":
      queryset = queryset.filter(customer_return=True)
  elif customer_return == "no":
      queryset = queryset.filter(customer_return=False)
  ```
  Rỗng → không lọc (mặc định hiển thị tất cả).

### 3.5. Views — nạp filter cho cả 3 endpoint
- `order_list_view.py`: thêm `"customer_return": source.get("customer_return", "")` vào dict `filters` của **cả 3** hàm: `list_orders`, `list_koc_orders`, `list_landing_orders`.

## 4. Frontend

Frontend root: `frontend/src`. Các trang dùng chung `OrdersPage`.

### 4.1. Type
- `order-types.ts` — `OrderRow`: thêm `customer_return?: boolean;`

### 4.2. Cột (hiện mặc định cả 3 trang)
- `order-types.ts`:
  - `OrderColumnKey`: thêm `"customer_return"`.
  - `ORDER_COLUMN_OPTIONS`: thêm `{ key: "customer_return", label: "orders.columns.customerReturn" }`.
  - `ORDER_COLUMN_FIELD_BY_KEY`: thêm `customer_return: "customer_return"`.
  - `DEFAULT_ORDER_COLUMNS`: thêm `"customer_return"` → bật sẵn. Vì KHÔNG thuộc `RECALL_COLUMN_KEYS` nên hiện trên cả Đơn hàng/Landing/KOC (KOC không bị filter bỏ).
- **i18n:** thêm key `orders.columns.customerReturn` = "Khách trả hàng" vào file dịch (theo đúng nơi định nghĩa các key `orders.columns.*` hiện có).
- `order-grid-columns.tsx` — `createOrderColumnDefs()`: thêm `ColDef<OrderRow>` cho `customer_return`:
  - Hiển thị **checkbox tick trực tiếp** trên ô; onChange → gọi PATCH inline (theo cơ chế lưu inline của lưới hiện tại).
  - Header tooltip: "Khách trả lại hàng do không còn nhu cầu — không phải đơn hoàn/thu hồi".

### 4.3. Sửa trong form chi tiết
- `OrderDetailDialog.tsx` (hoặc Block phù hợp, vd cạnh `BlockFinancials`): thêm 1 checkbox "Khách trả hàng", onChange → `patch({ customer_return })` — đúng pattern `patch({ customer_prepaid })` trong `BlockFinancials.tsx`.

### 4.4. Bộ lọc (dùng chung 3 trang)
- `OrdersFilterPanel.tsx`: thêm 1 ô select 3 trạng thái **Tất cả / Có / Không** (copy pattern filter "Đã in hóa đơn giao" `is_delivery_invoice_printed`).
- `use-orders-filters.ts`: thêm state filter `customerReturn` + reset.
- `use-orders-data.ts`:
  - `buildBody`: thêm `customer_return` (`"yes" | "no"`) vào body khi có giá trị.
  - `rowPassesActiveFilters`: thêm nhánh check `customer_return` (lọc in-memory) — bám pattern `is_delivery_invoice_printed`.
  - `clearFilters` / `LoadOrdersOverrides`: thêm trường tương ứng.

## 5. Tests

- Backend (`test_order_list_filters_api.py`, mirror case `is_delivery_invoice_printed`):
  - `customer_return=yes` chỉ trả đơn có `customer_return=True`.
  - `customer_return=no` chỉ trả đơn `False`.
  - Rỗng → trả tất cả.
  - Áp dụng/khẳng định trên cả 3 endpoint (orders, landing, koc) ở mức tối thiểu 1 endpoint + filter chung.
- Backend serializer: field `customer_return` có mặt trong output của list API.
- Backend inline-update: PATCH `customer_return=true` lưu thành công, GET trả về đúng.
- Frontend (nếu có test sẵn cho filter): bổ sung tương ứng theo `use-orders-filters.test.tsx` nếu phù hợp.

## 6. Ngoài phạm vi (YAGNI)

- Không tạo model/bảng riêng cho việc trả hàng.
- Không gắn `customer_return` với luồng `OrderRecall`/KiotViet.
- Không thêm index DB.
- Không thêm lịch sử thay đổi (journey event) cho cờ này.

## 7. Danh sách file chạm

**Backend**
- `backend/apps/orders/models.py`
- `backend/apps/orders/migrations/0075_order_customer_return.py` (mới)
- `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py`
- `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py`
- `backend/apps/orders/repositories.py`
- `backend/apps/orders/transport/views/order_views_impl/order_list_view.py`
- `backend/apps/orders/tests/test_order_list_filters_api.py` (+ test serializer/inline nếu tách file)

**Frontend**
- `frontend/src/features/orders/order-types.ts`
- `frontend/src/features/orders/order-grid-columns.tsx`
- `frontend/src/features/orders/OrderDetailDialog.tsx` (hoặc Block tương ứng)
- `frontend/src/features/orders/OrdersFilterPanel.tsx`
- `frontend/src/features/orders/use-orders-filters.ts`
- `frontend/src/features/orders/use-orders-data.ts`
- File i18n chứa các key `orders.columns.*`
