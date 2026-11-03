# Cột "Loại khách" (khách mới / khách cũ)

**Branch:** `feat/customer-type-column` — 2026-07-15

## Yêu cầu

Thêm cột **loại khách** (bool) vào bảng khách hàng: `true` = khách mới, `false` = khách cũ,
dựa trên khách **đã có > 1 đơn hàng hay chưa**. Hiển thị cột này ở bảng danh sách đơn hàng
trên các trang đơn (Đơn hàng / Landing / KOC / Đơn gửi ngoài / Vận đơn — mọi trang dùng
`OrdersTable`).

## Thiết kế

### Rule nghiệp vụ

- **Khách cũ = đã có ≥1 đơn giao thành công; khách mới = chưa có.**
  (`is_new_customer = NOT EXISTS(đơn giao thành công)`)
- **Đơn được tính** = chưa xóa mềm (`deleted_at IS NULL`) **và** `status = 3`
  (DELIVERED_SUCCESS) **và** hóa đơn không bị hủy (`invoice_status != 2` — loại đơn cũ
  khi KiotViet hủy-xuất lại hóa đơn HD→HD.01).
- Vì status đơn thay đổi theo vòng đời (webhook, sync), cờ phụ thuộc trạng thái →
  signal phải bắt cả save đổi `status`/`invoice_status`, không chỉ tạo/xóa. Các đường
  `queryset.update(status=...)` bypass signal do reconcile cron 2h vá.

### Backend

| Thành phần | File | Vai trò |
|---|---|---|
| Cột DB | `apps/customers/models.py` + migration `0029` | `is_new_customer` bool default `True`, backfill SQL set `False` cho khách có ≥1 đơn giao thành công |
| Recompute | `apps/customers/customer_type.py` | `refresh_customer_type(ids)` (2 UPDATE theo đếm) + `reconcile_customer_type()` (full-scan SQL, chỉ sửa dòng lệch) |
| Signal | `apps/customers/signals.py` (wire ở `apps.py`) | `post_save`: tạo đơn, full save (`update_fields=None` — webhook upsert), hoặc save đụng `status`/`invoice_status`/`deleted_at`/`customer`; + `post_delete` |
| Bulk delete | `orders/.../order_action_view.py` | soft delete bằng `queryset.update` bypass signal → gọi thẳng `refresh_customer_type` |
| Lưới an toàn | `apps/customers/tasks.py` | `reconcile_customer_type` chạy kèm cron `rebuild_segment_snapshot` (2h) — vá drift từ đường hiếm (đổi customer của đơn, xóa cứng, sửa tay DB) |
| API | `OrderSerializer.customer_is_new` (source `customer.is_new_customer`) | list orders đã `select_related("customer")` sẵn → không thêm query |
| Export Excel | `orders/export_excel.py` | key `customer_type` → "Khách mới"/"Khách cũ" |

### Frontend

- `order-types.ts`: `OrderRow.customer_is_new`, column key `customer_type`, option
  "Loại khách", field map → `customer_is_new`.
- `OrderCell.tsx`: badge — xanh "Khách mới" / xám "Khách cũ".
- `order-storage.ts`: thêm vào `DEFAULT_ORDER_COLUMNS` + `FORCED_COLUMN_MIGRATIONS`
  (`desi_order_customer_type_injected`) để user cũ (đã lưu cột trong localStorage) cũng
  thấy cột một lần; sau đó tôn trọng ẩn/hiện của user.
- `OrderDetailPage.tsx`: thêm InfoRow "Loại khách".
- i18n vi/en, regen `schema.yaml` + `schema.d.ts`.

## Quyết định & trade-off

1. **Cột denormalized thay vì tính lúc đọc**: đúng yêu cầu "thêm cột vào bảng khách hàng",
   đọc list không tốn thêm query, và tái dùng được cho trang KH/filter sau này. Rủi ro
   drift được chặn 3 lớp: signal (mọi luồng ORM) → gọi thẳng ở bulk delete (bypass
   signal) → reconcile cron 2h.
2. **Loại khách là trạng thái HIỆN TẠI của khách**, không phải "tại thời điểm đơn": khách
   đã có đơn giao thành công sẽ hiện "Khách cũ" trên MỌI dòng đơn của khách đó, kể cả
   chính đơn giao thành công đầu tiên (sau khi nó chuyển status=3) và các đơn chưa giao.
   Hệ quả tự nhiên: khách quay lại đặt đơn mới → đơn mới hiện "Khách cũ" ngay.
3. Lịch sử rule: v1 ">1 đơn mọi trạng thái" → v2 ">1 đơn giao thành công" → **v3 (chốt,
   theo yêu cầu 15/07): "≥1 đơn giao thành công là khách cũ"**.

## Deploy notes

- `python manage.py migrate customers` — migration `0029` tự backfill (1 UPDATE, dùng
  index FK `orders.customer_id`).
- Không cần lệnh tay thêm; reconcile chạy tự động theo beat `rebuild_segment_snapshot`.

## Test

- BE: `apps/customers/tests/test_customer_type.py` (11 test: signal tạo/xóa, soft delete
  save + bulk delete API, refresh 2 chiều, reconcile, serializer qua `/api/orders`).
  Suite liên quan: 201 passed. Ruff CI-set (E9,F63,F7,F82) pass.
- FE: lint 0 error, `tsc --noEmit` sạch, `vitest src/features/orders` 107 passed.

## Chưa làm (follow-up nếu cần)

- Filter theo loại khách trên trang đơn / trang khách hàng.
- Hiển thị cột ở trang Khách hàng (profiles) — data đã có sẵn trong DB.
