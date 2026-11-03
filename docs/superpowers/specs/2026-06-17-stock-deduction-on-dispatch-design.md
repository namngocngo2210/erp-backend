# Thiết kế: Trừ/hoàn tồn kho theo trạng thái xuất kho

- **Ngày:** 2026-06-17
- **Trạng thái:** ✅ Đã triển khai (branch feature/stock-deduction-on-dispatch)
- **Liên quan:** [[shipments-page-feature]], `docs/erp-inventory.md`

## 1. Mục tiêu

Trừ tồn kho sản phẩm khi đơn chuyển trạng thái vận chuyển thực (`delivery_business_status_code`)
sang **"Đã xuất kho" (`dispatched`)**, và tự hoàn tồn kho khi đơn **"Đã chuyển hoàn"
(`OrderStatus.RETURNED`)** hoặc **"Đã hủy" (`OrderStatus.CANCELLED`)** — kể cả khi trạng thái
đổi do đồng bộ từ KiotViet.

## 2. Quyết định (đã chốt với product owner)

| # | Quyết định | Giá trị |
|---|-----------|---------|
| 1 | Điểm trừ kho | **Chỉ khi `dispatched`** — BỎ trừ kho ở lúc confirm (`order.status → PENDING_PROCESSING`) |
| 2 | Nguồn kho | **Tái dùng `Order.fulfillment_warehouse`** (FK Warehouse, đã có) — không thêm cột DB |
| 3 | Thiếu kho khi dispatched | **Chặn** chuyển `dispatched` nếu chưa chọn kho (báo lỗi, rollback) |
| 4 | Hoàn kho | Khi `order.status → RETURNED` **hoặc** `CANCELLED` (nếu đã từng trừ) |
| 5 | Hoàn từ KiotViet | **Có** — status đổi do đồng bộ KiotViet cũng kích hoạt hoàn kho |
| 6 | UI | Thêm cột **"Kho xuất"** (inline select) trên trang Vận đơn |

## 3. Bối cảnh kỹ thuật (đã khảo sát)

- Tồn kho **LOCAL authoritative** (không sync từ KiotViet) → trừ nội bộ không double-count.
- `StockSalesService.issue_for_order(order_id, warehouse_id, lines, user_id)` — trừ kho,
  **idempotent** (guard theo movement `SALE` của order). `release_for_order(order_id)` — hoàn,
  **idempotent** (guard `SALE_REVERSAL`). Tái dùng cả hai, không sửa.
  (`apps/inventory/application/services/stock_sales_service.py`)
- `delivery_business_status_code` là field CRM thủ công; **KiotViet không set** field này →
  deduct chỉ qua inline edit ở trang Vận đơn (`update_order_inline`).
- `order.status` đổi ở nhiều nơi: inline edit (`order_service_commands.py`) **và** đồng bộ KiotViet
  (`apps/kiotviet/webhook_impl.py:510`, `infrastructure/services/service_actions.py:139`,
  `_sync_service_sync_mixin.py`).
- Event bus in-process Phase 0 có sẵn: `apps/common/events.py` (`event_bus.publish_on_commit`,
  `@subscribe(event_type)`).
- Transition detection cho assessment đã có: `_upsert_order_assessment` trả `changed_fields` +
  capture `original_values` (`order_service_helpers.py:481`).
- `OrderSerializer` đã trả `fulfillment_warehouse` (id) + `fulfillment_warehouse_name`
  (`order_core_serializers.py:151,226-227`).
- `OrderInlineUpdateSerializer` **đã nhận** `fulfillment_warehouse` (required=False, allow_null)
  (`kiotviet_landing_serializers.py:234,322-325`).
- Endpoint list kho: `GET /api/warehouses/` (`warehouses-list-create`).

## 4. Kiến trúc

```
DEDUCT (thủ công, trang Vận đơn)
  inline edit delivery_business_status_code → "dispatched"
   └─ update_order_inline (orders, @transaction.atomic)
        └─ _upsert_order_assessment phát hiện "dispatched" ∈ changed_fields
             ├─ nếu fulfillment_warehouse_id is None → raise ValidationError (rollback)
             └─ StockSalesService.issue_for_order(order_id, warehouse_id, lines=order.items)

RELEASE (status → RETURNED/CANCELLED, từ inline HOẶC KiotViet) — event-driven
  bất kỳ nơi nào set order.status (inline + KiotViet sync)
   └─ publish_on_commit  OrderStatusChanged(order_id, old_status, new_status)
        └─ subscriber (inventory): nếu new ∈ {RETURNED(5), CANCELLED(6)}
             └─ StockSalesService.release_for_order(order_id)   [idempotent]
```

## 5. Backend — chi tiết

### 5.1 Bỏ trừ kho ở confirm
`apps/orders/application/services/order_service_commands.py:340-363` — gỡ nhánh
`if order.status == OrderStatus.PENDING_PROCESSING: issue_for_order(...)`. Phần release ở
`CANCELLED` sẽ chuyển sang cơ chế event (5.4), không gọi trực tiếp ở đây nữa.

### 5.2 Trừ kho khi dispatched
Trong `update_order_inline`, sau khi `_upsert_order_assessment` trả `changed_assessment_fields`:
- Nếu `"delivery_business_status_code" ∈ changed` và giá trị mới = `"dispatched"`:
  - Nếu `order.fulfillment_warehouse_id is None` → `raise ValidationError({"fulfillment_warehouse":
    "Hãy chọn kho xuất trước khi chuyển sang Đã xuất kho."})` (transaction rollback → không lưu).
  - Ngược lại: `lines = [SaleLine(it.product_id, it.quantity) for it in order.items.all()]`;
    gọi `StockSalesService.issue_for_order(order_id=order.id,
    warehouse_id=order.fulfillment_warehouse_id, lines=lines, user_id=actor_id)`; gom
    `stock_warnings` (tồn âm) trả về cho client như hiện tại.
- Đặt logic này thành 1 helper rõ ràng (vd `_apply_dispatch_stock(order, changed, actor)`) để
  `update_order_inline` gọn.

### 5.3 Domain event `OrderStatusChanged`
- Định nghĩa event ở contracts của orders (vd `apps/orders/contracts/order_events.py`):
  `OrderStatusChanged(order_id: int, old_status: int, new_status: int)` (kế thừa `DomainEvent`,
  `event_type = "orders.status_changed"`).
- Phát `event_bus.publish_on_commit(OrderStatusChanged(...))` tại MỌI nơi set `order.status` khi
  giá trị thực sự đổi:
  - `update_order_inline` (so `old_status` vs `order.status`).
  - KiotViet: `webhook_impl.py:510`, `service_actions.py:139`, và đường trong
    `_sync_service_sync_mixin.py` (xác định điểm apply status thực tế trong khi lập plan).
- Chỉ phát khi `old != new`.

### 5.4 Subscriber hoàn kho
- `apps/inventory/subscribers.py`: thêm handler `@subscribe("orders.status_changed")`:
  - Nếu `event.new_status ∈ {OrderStatus.RETURNED, OrderStatus.CANCELLED}` →
    `StockSalesService.release_for_order(order_id=event.order_id)`.
  - Idempotent nên an toàn nếu KiotViet sync lặp / nhiều event.
- Lưu ý ranh giới module: KiotViet/orders chỉ **phát event**; inventory **subscribe** → không phụ
  thuộc ngược. Đảm bảo subscriber được nạp (app `inventory` `ready()` import subscribers — kiểm tra
  cơ chế đăng ký hiện có cho `StockWentNegative`).

### 5.5 Field policy cho fulfillment_warehouse (inline)
`OrderInlineUpdateSerializer` đã nhận `fulfillment_warehouse`. Kiểm tra `FieldPolicyWriteMixin` /
`order_field_policy` cho phép user section `shipments` ghi field này (nếu policy chặn theo field,
bổ sung quyền ghi `fulfillment_warehouse` cho luồng vận đơn).

## 6. Frontend — cột "Kho xuất" trên trang Vận đơn

- **OrderRow type** (`features/orders/order-types.ts`): bổ sung `fulfillment_warehouse?: number | null`
  và `fulfillment_warehouse_name?: string | null` (khớp serializer).
- **Cột mới** trong `OrdersTable` (`components/OrdersTable.tsx`): key `fulfillment_warehouse` —
  render select inline danh sách kho; chọn → `onPatch(id, { fulfillment_warehouse: <id> })`.
  Đăng ký key trong `ORDER_COLUMN_OPTIONS` + `ORDER_COLUMN_FIELD_BY_KEY` (label "Kho xuất").
- **Options kho**: nạp từ `GET /api/warehouses/` (hook nhỏ trong trang Vận đơn, truyền options vào
  bảng/cột). Cân nhắc truyền qua prop để OrdersTable không tự fetch.
- **SHIPMENT_COLUMNS** (`features/inventory/shipments/shipment-columns.ts`): thêm
  `"fulfillment_warehouse"` (vd ngay sau `shipping_code`).
- **use-shipments**: `patchOrder` đã PATCH `/api/orders/:id` — gửi `{ fulfillment_warehouse }`.
  Khi đổi `delivery_business_status` sang dispatched mà backend trả lỗi thiếu kho (400) → hiển thị
  toast lỗi (đã có `handlePatch` bắt lỗi; đảm bảo hiện message từ server).

## 7. Edge cases

- Đơn không có `OrderItem` → `issue_for_order` lặp rỗng, không trừ (no-op). Chấp nhận.
- Tồn âm: cho phép, phát `StockWentNegative` (cơ chế sẵn có) → trả `stock_warnings` cho client.
- Đơn đã dispatched (đã có SALE) rồi set dispatched lại → `issue_for_order` idempotent → no-op.
- Đơn đã RETURNED/CANCELLED nhiều lần (KiotViet sync lặp) → `release_for_order` idempotent → no-op.
- Đơn dispatched chưa từng trừ (vd thiếu kho bị chặn nên chưa có SALE) mà sau bị RETURNED →
  `release_for_order` không có SALE để đảo → no-op an toàn.

## 8. Kiểm thử

- **Inventory/orders service:**
  - dispatched + có kho → trừ đúng số lượng, tạo movement SALE.
  - dispatched + KHÔNG kho → ValidationError, KHÔNG tạo movement, status không lưu.
  - set dispatched 2 lần → chỉ trừ 1 lần (idempotent).
  - status → RETURNED (qua event) → hoàn kho (SALE_REVERSAL); gọi lại → no-op.
  - status → CANCELLED → hoàn kho.
  - **KiotViet path**: giả lập sync set status=RETURNED → event phát → tồn được hoàn.
- **Cập nhật test cũ:** `apps/orders/tests/test_order_stock_integration.py` đang test
  trừ-khi-confirm — sửa lại theo hành vi mới (confirm KHÔNG còn trừ; dispatched mới trừ).
- **Frontend:** smoke render cột Kho xuất; chọn kho → PATCH; dispatched thiếu kho → toast lỗi.

## 9. Ngoài phạm vi / rủi ro

- Không đụng cơ chế tồn kho lõi (`StockCommandService`, movements, projection).
- Các đường set `delivery_business_status` KHÁC inline edit (import/tasks) không kích hoạt deduct —
  chấp nhận (nghiệp vụ kho dùng inline edit). Ghi log/ghi chú nếu cần mở rộng sau.
- Rủi ro: nếu một điểm set `order.status` trong KiotViet bị bỏ sót không phát event → đơn đó không
  tự hoàn. Khi lập plan phải liệt kê ĐẦY ĐỦ các điểm apply status và bọc event.
- `fulfillment_warehouse` phải được map đúng tới kho vật lý nơi hàng thực xuất; nghiệp vụ chọn kho
  do nhân viên kho thực hiện trên UI.

## 10. Ghi chú triển khai

- **Trừ kho khi dispatched:** `update_order_inline` gọi `_apply_dispatch_stock(order, changed, actor)` — nếu `delivery_business_status_code` chuyển sang `dispatched` mà `fulfillment_warehouse_id is None` thì raise `ValidationError` (rollback toàn bộ transaction); ngược lại gọi `StockSalesService.issue_for_order`.
- **Hoàn kho qua event:** `OrderStatusChanged(order_id, old_status, new_status)` được phát (`publish_on_commit`) tại mọi điểm set `order.status` (inline + KiotViet sync). Subscriber `release_for_order` trong `apps/inventory/subscribers.py` bắt `new_status ∈ {RETURNED, CANCELLED}` và gọi `StockSalesService.release_for_order` — idempotent, an toàn khi lặp.
- **KiotViet sync đã được instrument đủ 5 điểm:** `webhook_impl.py`, `service_actions.py`, và 3 đường trong `_sync_service_sync_mixin.py` (bulk-status, single-order apply, fallback). Sync mixin chỉ phát event, **không ghi DB trực tiếp**. Lệnh `bulk_import` được loại trừ có chủ đích — import lịch sử không kích hoạt deduct.
- **UI:** Tái dùng `Order.fulfillment_warehouse` (FK có sẵn, không thêm cột DB). Cột inline **"Kho xuất"** trên trang Vận đơn (`SHIPMENT_COLUMNS`) render select từ `GET /api/warehouses/` (chỉ kho active). PATCH qua endpoint hiện có `/api/orders/:id`.
- **Kết quả kiểm thử:** 25/25 test mới pass (5 file: `test_dispatch_stock`, `test_order_stock_integration`, `test_shipment_orders_api`, `test_release_on_status_change`, `test_status_change_releases_stock`). Frontend build thành công (`✓ built in 14.64s`).
