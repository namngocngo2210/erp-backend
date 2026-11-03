# Thiết kế: Cột VAT cho đơn hàng

## 1. Bối cảnh & Mục tiêu
Bổ sung trường `vat` (hình thức xuất hoá đơn VAT) cho đơn hàng, hiển thị và
chỉnh sửa inline ngay trên bảng danh sách đơn hàng (cả tab đơn thường lẫn tab
đơn landing — hai tab dùng chung component `OrdersTable`).

Enum gồm 3 giá trị:

| Giá trị (DB) | Nhãn hiển thị | Ý nghĩa |
|--------------|---------------|---------|
| `individual` | Cá nhân       | Xuất VAT cho cá nhân |
| `company`    | Công ty       | Xuất VAT cho công ty |
| `no`         | Không xuất    | Không xuất hoá đơn (mặc định) |

## 2. Thiết kế kỹ thuật

### Backend (Django)
- `apps/orders/models.py`
  - Thêm enum `OrderVat(models.TextChoices)`.
  - Thêm field `vat = CharField(max_length=20, choices=OrderVat.choices, default=OrderVat.NO)`.
    Chọn default `no` để tránh null và phản ánh đúng đa số đơn không xuất VAT.
- Migration `0080_order_vat.py`: `AddField` thuần, additive, có default → an toàn,
  không khoá bảng lâu, không mất dữ liệu.
- Serializers:
  - `OrderSerializer` (list/detail): thêm `vat` vào `Meta.fields`.
  - `OrderInlineUpdateSerializer` (PATCH inline): thêm `vat` + `validate_vat()`
    chặn giá trị ngoài enum (HTTP 400).

### Frontend (React + ag-grid)
- `features/orders/vat.ts`: nguồn chân lý cho nhãn/màu badge/options (theo pattern
  `order-source.ts`): `ORDER_VAT_LABELS`, `ORDER_VAT_OPTIONS`, `OrderVatBadge`.
- `features/orders/order-types.ts`: thêm type `OrderVat`, field `vat` trên `OrderRow`
  và `CreateOrderPayload`, key `"vat"` trong `OrderColumnKey` / `ORDER_COLUMN_OPTIONS`
  / `ORDER_COLUMN_FIELD_BY_KEY`, và thêm `vat` vào `DEFAULT_ORDER_COLUMNS` (hiện mặc định).
- `features/orders/order-grid-columns.tsx`: định nghĩa cột `vat` dùng
  `createSelectCellRenderer` → dropdown chọn inline + badge màu; chỉ sửa được khi
  `can_edit`. Patch tự động đi qua `onPatch` sẵn có.
- `i18n/messages/orders.ts`: key `orders.columns.vat` và `orders.placeholders.selectVat`
  (vi + en).

## 3. Sprint & Tiến độ

### Sprint 1 — Cột VAT inline (DONE)
- [x] Backend: enum + field + migration 0080
- [x] Backend: serializer list + inline update + validate
- [x] Frontend: vat.ts, types, column def, i18n, default column
- [x] Unit test backend: default `no`, expose ở serializer, patch inline, reject giá trị sai (4/4 pass)
- [x] `python manage.py check` OK · `tsc --noEmit` OK · lint file mới sạch
- [ ] **Apply migration trên DB** — defer tới bước deploy (giống warehouse). Lệnh:
  `docker exec desi-web python manage.py migrate orders`

### Sprint 2 — Hiển thị mặc định + trang detail (DONE)
Phát sinh sau khi test thực tế: cột VAT không tự hiện trên bảng và trang detail
chưa có ô chọn.
- [x] **Hiển thị cột thật:** bảng đơn lấy cột hiển thị từ `getStoredOrderColumns()`
  trong `order-storage.ts` (khác bản `DEFAULT_ORDER_COLUMNS` ở `order-types.ts` —
  bản kia chỉ dùng cho cột export). Đã thêm `vat` vào default của `order-storage.ts`
  và thêm `FORCED_COLUMN_MIGRATIONS` (`desi_order_vat_injected`) để **inject 1 lần**
  cho user cũ đã lưu cấu hình cột (giống cách làm với `kiotviet_state`). Áp dụng cho
  cả danh sách đơn thường lẫn danh sách landing (migration theo từng storage key).
- [x] **Trang detail** (`OrderDetailPage.tsx`): thêm `ERow` "VAT" — badge + select sửa
  trực tiếp, gọi `patch({ vat })` như các field khác.
- [x] `tsc --noEmit` OK · `order-storage.test.ts` 7/7 pass.

### Sprint 3 — Form tạo/sửa đơn (DONE)
Phát hiện khi test: form tạo đơn chưa có ô chọn VAT, và backend create chưa nhận field.
- [x] **Backend create:** `OrderCreateSerializer` (Serializer thuần, không phải
  ModelSerializer) thêm `vat = ChoiceField(choices=OrderVat.choices, default=NO)`;
  `OrderService.create_order_manual` truyền `vat` vào `Order.objects.create(...)`
  (import `OrderVat` qua `order_service_shared`).
- [x] **Frontend payload:** `order-payload.ts` thêm `vat` vào `CreateOrderPayload` +
  `sanitizeCreateOrderPayload` (gửi khi tạo) + guard trong `sanitizePatchOrderPayload`
  (vat là NOT NULL → bỏ qua khi rỗng thay vì gửi null gây 400).
- [x] **Form UI:** `OrderFormDialog.tsx` thêm ô select VAT (cạnh "Nguồn đơn"),
  default `no`, init từ `order.vat` khi sửa.
- [x] Test backend: tạo đơn có VAT + default NO (6/6 pass); `order-payload.test.ts`
  4/4; `tsc --noEmit` OK.

### Sprint 4 — Sửa inline thật sự trên bảng danh sách (DONE)
Phát hiện khi test: cột VAT hiện ra nhưng **không chọn được inline** ngay ở ô.
- Nguyên nhân: có **2 grid riêng**. `order-grid-columns.tsx` (tôi đã thêm cột Select)
  chỉ dùng bởi `OrdersGridDesktop.tsx` — **không được render**. Trang danh sách thực tế
  dùng `OrdersTable.tsx`, vốn tự render từng cột bằng `switch` (`renderCell`), không có
  case cho `vat` → rơi vào hiển thị text thường.
- [x] Thêm `case "vat"` trong `OrdersTable.tsx`: badge khi không sửa được, `<select>`
  (3 lựa chọn) + `onPatch({ vat })` khi `can_edit` — đồng nhất pattern `customer_gender`.
  Phủ cả danh sách đơn thường lẫn landing (cùng component).
- [x] `tsc --noEmit` OK.

### Đề xuất tiếp theo (chưa làm)
- Bộ lọc theo VAT ở thanh filter nếu nghiệp vụ cần thống kê đơn cần xuất hoá đơn.
- Dọn `OrdersGridDesktop.tsx` + `order-grid-columns.tsx` nếu xác nhận là code chết.

## 4. Ghi chú
- Cột VAT được inject 1 lần cho user cũ qua `FORCED_COLUMN_MIGRATIONS`; sau đó tôn
  trọng lựa chọn ẩn/hiện của user.
