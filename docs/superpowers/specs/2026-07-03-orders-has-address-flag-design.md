# Orders `has_address` flag — thiết kế

**Ngày:** 2026-07-03
**Trạng thái:** Đã duyệt

## Mục tiêu

Thêm cột `orders.has_address` (bool) ghi nhận sự kiện **"đơn đổ về qua API ingest mà không có địa chỉ"** tại thời điểm ingest, phục vụ báo cáo/phân loại nguồn đơn.

## Yêu cầu đã chốt

- `has_address` mặc định `true`; đơn ingest về **không có địa chỉ** → `false`.
- **Bất biến sau ingest:** sale điền địa chỉ sau đó thì flag vẫn giữ `false`. Muốn biết đơn HIỆN có địa chỉ hay chưa vẫn check field address như trước.
- **Phạm vi:** chỉ 2 luồng API ingest — webhook landing và Lark ingest. Đơn tạo tay trên CRM / import Excel giữ default `true`.
- **Không backfill:** toàn bộ đơn cũ nhận `true`; flag chỉ có ý nghĩa từ thời điểm deploy.

## Thiết kế

### Model + migration

- `Order.has_address = models.BooleanField(default=True)` — NOT NULL.
- Migration thêm cột với default; Postgres 11+ chỉ ghi metadata, không rewrite bảng orders lớn.
- Không đánh index (boolean cardinality thấp, chưa có query filter). Khi cần lọc "đơn thiếu địa chỉ" sẽ thêm partial index `WHERE NOT has_address`.

### Điểm set flag

1. **Landing** — `OrderServiceCommands.create_order_from_landing`: thêm `has_address=bool(raw_address)` vào `Order.objects.create(...)`. `raw_address` tại đây đã qua `normalize_raw_address` (placeholder "không có địa chỉ giao hàng" đã strip thành rỗng) — "không địa chỉ" = chuỗi rỗng sau normalize, nhất quán với logic phân tab tư vấn hiện có.
2. **Lark** — view `lark_ingest`: sau khi `create_order_manual` trả về order, nếu payload không có cả `raw_address` lẫn `address` → set `has_address=False`, save riêng field. Không truyền cờ vào `create_order_manual` để luồng tạo tay không bị ảnh hưởng.

### API

- Expose `has_address` trong `OrderSerializer.Meta.fields` (GET). PATCH inline đi qua `OrderInlineUpdateSerializer` riêng nên flag không sửa được qua API — đảm bảo bất biến.

### Test

- Landing: payload có địa chỉ → `true`; không địa chỉ / placeholder address → `false`.
- Lark: payload không địa chỉ → `false`; có địa chỉ → `true`.

## Bộ lọc theo has_address (bổ sung, đã duyệt)

- **Phạm vi:** trang Đơn hàng thường + Landing (user chốt "cả hai"). Vì `OrdersFilterPanel` dùng chung, mọi endpoint nhận body từ panel đều phải parse key để tránh lỗi "UI có mà không lọc": `build_orders_filters` (kiêm export Excel), `list_landing_orders`, `list_koc_orders`, `list_sheet3_orders`.
- **Backend:** `_apply_list_filters` nhánh `has_address` theo pattern yes/no hiện có (`is_delivery_invoice_printed`): `"yes"` → `has_address=True`, `"no"` → `has_address=False`, rỗng → bỏ qua.
- **Index:** partial index `(created_at) WHERE has_address = false` — tập "không địa chỉ" nhỏ, phục vụ lọc + sort ngày; ca "yes" là đa số hàng, không cần index.
- **Frontend:** select "Địa chỉ khi đổ đơn" (`Tất cả`/`Có địa chỉ`/`Không có địa chỉ` = `""`/`"yes"`/`"no"`) trong `OrdersFilterPanel`; plumbing theo dây `isDeliveryInvoicePrinted` (storage → filters state → body build + client-side match → page props → pass-through App/RootLayout/routed-pages); `OrderRow.has_address?: boolean`.
- **Ngữ nghĩa hiển thị:** lọc theo cờ bất biến lúc ingest — đơn đã được sale điền địa chỉ sau vẫn thuộc nhóm "Không có địa chỉ".

## Phương án đã loại

- **Computed property từ address rỗng** — không giữ được tính bất biến "tại thời điểm ingest" khi sale sửa địa chỉ sau.
- **Signal / override `Order.save()`** — side effect ẩn, khó trace, ảnh hưởng mọi luồng tạo đơn.
