# Thiết kế: Trường "Ngày hoàn hàng" cho đơn hàng

## 1. Bối cảnh & Mục tiêu
Bổ sung trường `return_date` (ngày khách trả/hoàn hàng về kho) cho đơn hàng, với 3 yêu cầu:
1. **Hiển thị + sửa inline** ngay trên bảng danh sách đơn (`OrdersTable.tsx`, dùng chung
   cho cả tab đơn thường lẫn tab landing).
2. **Sửa inline bằng date picker** (`<input type="date">` native — không thêm dependency),
   cho phép **xóa** (đặt về trống), **lưu ngay** khi chọn (`onChange`).
3. **Bộ lọc theo tuần / theo tháng** áp **riêng cho `return_date`** (tách biệt với bộ lọc
   `created_at` đang có).

### Quyết định nghiệp vụ (đã chốt với CTO)
- `return_date` là **field độc lập mới**, nhập tay — **không** gắn với cờ `customer_return`
  hiện có, **không** tái dùng luồng `OrderRecall.recall_received_date` (thu hồi KOC).
- Kiểu dữ liệu: **`DateField`** (chỉ ngày, không giờ) — đúng ngữ nghĩa "ngày hoàn".
- Bộ lọc tuần/tháng lọc theo `return_date`, **tách riêng** khỏi bộ lọc theo ngày tạo đơn.

## 2. Thiết kế kỹ thuật

### Backend (Django)
- `apps/orders/models.py`
  - Thêm `return_date = models.DateField(null=True, blank=True, db_index=True)`
    — comment "Ngày hoàn hàng (KH trả về)". `null=True` vì đa số đơn không có.
    `db_index=True` vì sẽ lọc theo field này.
- Migration `0084_order_return_date.py`: `AddField` thuần, additive, nullable → an toàn,
  không khóa bảng lâu, không mất dữ liệu.
- Serializers:
  - `OrderSerializer` (list/detail): thêm `return_date` vào `Meta.fields` (đọc).
  - `OrderInlineUpdateSerializer` (PATCH inline): thêm `return_date` vào `Meta.fields`.
    `DateField` của DRF chấp nhận `null` → xóa được. Không cần validate enum.
- Filter (lọc theo tuần/tháng cho `return_date`):
  - `build_orders_filters()` (`_view_helpers.py`): nhận thêm `return_date_from` /
    `return_date_to` (ISO date string), đưa vào dict filter.
  - `repositories.py`: thêm `_apply_return_date_filters()` — tách biệt với
    `_apply_created_at_date_filters()`, dùng lại logic boundary inclusive
    (`_parse_local_datetime_boundary` hoặc so sánh `date` trực tiếp vì là `DateField`).
    Gọi trong `OrderRepository.list_orders()`.

### Frontend (React)
> **Lưu ý bám codebase (rút từ tài liệu cột VAT `order-vat-column.md`):**
> - Bảng danh sách **thật** là `OrdersTable.tsx`, render từng cột bằng `switch`/`renderCell`
>   → phải thêm `case "return_date"`. (`OrdersGridDesktop.tsx` + `order-grid-columns.tsx`
>   là grid khác, không được render — bỏ qua.)
> - Cột hiển thị thực tế lấy từ `getStoredOrderColumns()` trong `order-storage.ts`,
>   **không** phải `DEFAULT_ORDER_COLUMNS` ở `order-types.ts` (bản kia chỉ dùng cho export).
>   → phải thêm `return_date` vào default của `order-storage.ts` **và** thêm một
>   `FORCED_COLUMN_MIGRATIONS` (vd `desi_order_return_date_injected`) để inject 1 lần cho
>   user cũ đã lưu cấu hình cột (áp cho cả storage key đơn thường lẫn landing).

- `features/orders/order-types.ts`:
  - Thêm `return_date?: string | null` vào `OrderRow`.
  - Thêm key `"return_date"` vào `OrderColumnKey`, `ORDER_COLUMN_OPTIONS`,
    `ORDER_COLUMN_FIELD_BY_KEY`, và `DEFAULT_ORDER_COLUMNS` (cho export).
- `components/OrdersTable.tsx`: thêm `case "return_date"` trong `renderCell`:
  - Hiển thị ngày đã format `dd/MM/yyyy`; placeholder `-` khi trống.
  - Khi `can_edit`: ô là `<input type="date">` (picker native của trình duyệt) —
    chọn ngày hoặc để trống. Gọi `onPatch(row.id, { return_date })` ngay khi `onChange`
    (`""` → `null` để xóa).
  - Khi không `can_edit`: chỉ hiển thị text (giống các cell inline khác — tôn trọng
    `can_edit_order`).
- `features/orders/order-storage.ts`: thêm `return_date` vào default columns +
  `FORCED_COLUMN_MIGRATIONS` inject 1 lần (đơn thường + landing).
- Bộ lọc UI:
  - `features/orders/use-orders-filters.ts`: thêm state `returnDateFilter`
    (kiểu `{ option, from, to }` như `dateFilter`), lưu localStorage; map sang
    `return_date_from` / `return_date_to` khi gọi API.
  - `features/orders/use-orders-data.ts`: đưa `return_date_from`/`return_date_to` vào
    body `POST /api/orders`.
  - `features/orders/OrdersFilterPanel.tsx`: thêm một `DateFilter` thứ hai nhãn
    **"Ngày hoàn hàng"** — tái dùng component `components/ui/date-filter.tsx` (đã có sẵn
    option `this_week`/`last_week`/`this_month`/`last_month`…). Không phải code mới phần option.
- `i18n/messages/orders.ts`: key `orders.columns.returnDate` + nhãn filter (vi + en).

### Tận dụng lại
- `components/ui/date-filter.tsx` đã có sẵn các option theo tuần/tháng → bộ lọc dùng lại 100%.
- Pattern inline cell theo `CustomerGenderCell` / `case "vat"` (cùng cơ chế `onPatch`).
- Date picker: dùng `<input type="date">` native — dự án **không** có `react-day-picker`/
  `date-fns`, nên tránh thêm dependency; value `yyyy-MM-dd` khớp đúng `DateField` của DRF.

## 3. Sprint & Tiến độ

### Sprint 1 — Backend: field + migration + serializer + filter
- [x] `models.py`: thêm `return_date` (DateField, null, db_index)
- [x] Migration `0084_order_return_date.py`
- [x] `OrderSerializer`: expose `return_date`
- [x] `OrderInlineUpdateSerializer`: nhận `return_date` (set + xóa null)
- [x] Filter: `return_date_from`/`return_date_to` ở `build_orders_filters` +
      `_apply_return_date_filters` ở `repositories.py`
- [x] Unit test: default null, expose ở serializer, patch set ngày, patch xóa (null),
      filter theo khoảng ngày
- [x] `python manage.py check` OK
- [ ] **Apply migration trên DB** — defer tới bước deploy:
      `docker exec desi-web python manage.py migrate orders`

### Sprint 2 — Frontend: cột inline + date picker
- [x] `order-types.ts`: type + column key + options + default export column
- [x] `OrdersTable.tsx`: `case "return_date"` — `<input type="date">` native, để trống = xóa
- [x] `order-storage.ts`: default column + `FORCED_COLUMN_MIGRATIONS` inject (thường+landing)
- [x] (N/A) không cần thêm shadcn calendar — dùng input native, không thêm dependency
- [x] `i18n`: `orders.columns.returnDate`
- [x] `tsc --noEmit` OK

### Sprint 3 — Frontend: bộ lọc theo tuần/tháng cho ngày hoàn
- [x] `use-orders-filters.ts`: state `returnDateFilter` + localStorage + map query param
- [x] `use-orders-data.ts`: gửi `return_date_from`/`return_date_to`
- [x] `OrdersFilterPanel.tsx`: `DateFilter` "Ngày hoàn hàng"
- [x] `i18n`: nhãn filter
- [x] `tsc --noEmit` OK · kiểm thử lọc tuần/tháng ra đúng đơn

### Sprint 4 — Fix: 2 DateFilter chung nhóm radio (DONE)
Phát hiện khi test thực tế: chọn bộ lọc "Ngày tạo" nhưng trạng thái nhảy sang "Ngày hoàn hàng".
- Nguyên nhân (lỗi latent có sẵn trong `components/ui/date-filter.tsx`): radio dùng
  `name="date-filter-mode"` cứng + `id="date-filter-from/to"` cứng. Khi chỉ có 1 instance
  thì không lộ; thêm instance thứ 2 (return_date) khiến cả 4 radio gộp thành **một nhóm
  radio chung toàn trang** → trình duyệt chỉ cho 1 radio checked, trạng thái nhảy giữa 2 bộ lọc.
- [x] Fix: dùng `useId()` sinh `name`/`id` duy nhất cho mỗi instance (`date-filter-mode-{id}`,
  `date-filter-from-{id}`, `date-filter-to-{id}`).
- [x] Test `date-filter.test.tsx`: render 2 instance, khẳng định radio `name` khác nhau +
  không trùng `id` (2/2 pass). `tsc --noEmit` OK.

### Đề xuất tiếp theo (chưa làm)
- Hiển thị + sửa `return_date` ở trang detail (`OrderDetailPage.tsx`) nếu nghiệp vụ cần.
- Thêm `return_date` vào form tạo/sửa đơn nếu muốn nhập ngay lúc tạo.
- Thống kê đơn hoàn theo tuần/tháng (dashboard) nếu cần báo cáo.

## 4. Ghi chú
- Migration additive nullable → an toàn, không cần backfill.
- Cột được inject 1 lần cho user cũ qua `FORCED_COLUMN_MIGRATIONS`; sau đó tôn trọng
  lựa chọn ẩn/hiện của user.
- Quyền sửa inline dùng đúng `can_edit_order` như các field inline hiện có.
