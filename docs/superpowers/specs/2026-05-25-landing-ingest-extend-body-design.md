# Thiết kế: Mở rộng body Landing Ingest API

- **Ngày:** 2026-05-25
- **Branch:** feat-landing-ingest-extend-body
- **Trạng thái:** Đã implement & test pass — 2026-05-25. Thêm 9 test mới (pass), fix 2 stale ingest test; không regression (so baseline master: failures 14→12, errors 13→13 — phần còn lại là tech debt sẵn có, không liên quan task này).

## 1. Bối cảnh & Mục tiêu

API landing ingest hiện tại (`POST` → [order_ingest_product_dashboard_view.py](../../../backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py))
nhận body qua [LandingOrderSerializer](../../../backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py)
với `channel_id` dạng **int**, và `quantity`/`total_amount` được **suy ra từ text** trường `product`.

Nguồn ADS (Lark) cần đẩy đơn với body:

| Cột nguồn | Field body | Trạng thái |
|-----------|-----------|-----------|
| ngày | `created_at` | đã có |
| tên khách hàng | `name` | đã có |
| số điện thoại | `phone` | đã có |
| địa chỉ | `address` | đã có |
| số lượng | `quantity` | **MỚI** |
| SKU | `product_id` | đã có (int id hoặc chuỗi SKU) |
| GIÁ | `total_amount` | **MỚI** |
| KÊNH ADS | `channel_id` | **THAY ĐỔI:** nhận text (vd `"Facebook Thành"`) ngoài int |

**Mục tiêu:** Mở rộng *chính endpoint landing ingest hiện tại* (không tạo endpoint mới) để
nhận thêm `quantity`, `total_amount`, và `channel_id` dạng text — đồng thời **giữ tương thích
ngược** với caller cũ (channel_id int, không có quantity/total_amount).

## 2. Quyết định thiết kế (đã chốt với người dùng)

1. **Endpoint:** Sửa landing ingest hiện tại, không tạo mới.
2. **Channel:** Field `channel_id` nhận **int (id) HOẶC text**. Text được map qua
   [_normalize_channel_code_or_original](../../../backend/apps/orders/import_excel.py) → `Channel.code`.
   Channel **vẫn bắt buộc**: nếu không resolve được (id/code không tồn tại) → **trả lỗi 400,
   không lưu DB**, ghi trace `VALIDATION_FAILED`. Không làm `Order.channel` nullable.
3. **quantity / SKU:** Tạo order item `SKU x quantity`. `quantity` tường minh override giá trị
   suy ra từ text.
4. **total_amount:** **`total_amount` trong body thắng.** Nếu body gửi `total_amount` → đây là
   tổng đơn cuối cùng, ghi thẳng vào `Order.total_amount` và **không** bị `_sync_order_totals_from_items`
   ghi đè từ sum giá catalog. Item vẫn được tạo theo SKU+quantity nhưng tổng đơn lấy theo body
   (giá khách trả thực tế từ ADS). Khi body **không** gửi `total_amount` → giữ logic cũ
   (sum item nếu có item, ngược lại derive từ text).

## 3. Thay đổi chi tiết

### 3.1. Serializer — `LandingOrderSerializer`

- `channel_id`: đổi từ `IntegerField()` → custom field (tên gợi ý `_ChannelRefField`) nhận
  **int hoặc string non-empty**; trả `None`/lỗi nếu rỗng. Giữ key `channel_id` để không phá body cũ.
- Bỏ `validate_channel_id` strict (chuyển việc resolve + kiểm tra tồn tại sang view, vì view đã có
  sẵn flow trace + lead filter).
- Thêm `quantity`: `IntegerField(required=False, allow_null=True, min_value=1)`.
- Thêm `total_amount`: `DecimalField(max_digits=12, decimal_places=2, required=False,
  allow_null=True, min_value=0)` — DRF parse được cả số lẫn chuỗi số.
- `validate()`: đưa `quantity`, `total_amount` (khi có) vào `attrs`.

### 3.2. View — `landing_ingest`

- Thêm helper `_resolve_channel_ref(raw) -> Channel | None`:
  - Nếu raw là int-like (int hoặc chuỗi toàn chữ số) → `Channel.objects.filter(id=..., deleted_at__isnull=True)`.
  - Ngược lại (text) → `code = _normalize_channel_code_or_original(raw)` →
    `Channel.objects.filter(code=code, deleted_at__isnull=True)`.
  - Trả `Channel` hoặc `None`.
- Thay block resolve channel hiện tại ([dòng ~151-168](../../../backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py))
  bằng `_resolve_channel_ref`. `None` → 400 + trace `VALIDATION_FAILED` (giữ message rõ ràng kèm giá trị nhận được).
- Sau khi resolve thành công, gán `payload["channel_id"] = channel.id` (int) trước khi `dispatch`,
  để [create_order_from_landing](../../../backend/apps/orders/application/services/order_service_commands.py)
  dùng nguyên logic resolve channel theo id — **không cần sửa phần channel trong service**.

### 3.3. Service — `create_order_from_landing`

- **quantity:** `landing_quantity = explicit payload["quantity"] nếu có, ngược lại derive_quantity(product_raw)`.
  Giá trị này áp vào item khi map 1 SKU (giữ logic `single_sku` hiện tại).
- **total_amount:** nếu `payload["total_amount"]` có giá trị → đặt `Order.total_amount` =
  giá trị này và **bỏ qua** `_sync_order_totals_from_items` (không ghi đè từ sum item).
  Item vẫn được `bulk_create` theo SKU+quantity để giữ chi tiết dòng. Cần điều chỉnh nhánh
  `if matched_items:` để chỉ chạy `_sync_order_totals_from_items` + `order.save(total_amount...)`
  khi **không** có `total_amount` tường minh.
  Nếu body không gửi `total_amount` → giữ logic cũ: `derived_total_amount =
  derive_total_amount(product_raw)`, có item thì sum item thắng.

### 3.4. Trace & Logging

- `LandingIngestTrace` đã lưu raw payload đầy đủ; bổ sung `quantity`/`total_amount` vào các dòng
  log `debug` validated payload cho dễ trace. Không cần thêm cột DB.

## 4. Tương thích ngược

- Body cũ: `channel_id` int → `_resolve_channel_ref` xử lý nhánh int-like, hành vi không đổi.
- Không `quantity`/`total_amount` → fallback về derive-từ-text như cũ.
- `_sync_order_totals_from_items` giữ nguyên → đơn có item không đổi tổng tiền.

## 5. Test (test_ingest_api.py)

1. **Channel text hợp lệ** (`"Facebook Thành"`) → 200, đơn gắn đúng channel.
2. **Channel text không map được** → 400, không tạo Order, trace `VALIDATION_FAILED`.
3. **Channel id int (body cũ)** → 200 (regression).
4. **quantity + SKU tường minh** → item `SKU x quantity` đúng số lượng.
5. **total_amount khi không map SKU** → `Order.total_amount` = giá trị body.
6. **total_amount khi CÓ item** → `Order.total_amount` = giá trị body (không bị sum item ghi đè),
   item vẫn được tạo đúng.
7. **Không gửi total_amount, có item** → tổng đơn = sum giá catalog (regression logic cũ).

## 6. Ngoài phạm vi (YAGNI)

- Không tạo endpoint mới, không batch/array body.
- Không làm `Order.channel` nullable.
- Không auto-create Channel khi text lạ.
- Không sửa endpoint `lark_ingest` (đã có luồng riêng).
