# Inventory Stock Hardening — Design

- **Ngày:** 2026-07-02
- **Branch:** `feat/inventory-stock-hardening` (từ `master`)
- **Phạm vi:** Củng cố tầng xử lý kho (`apps/inventory`) theo 3 workstream: (A) test lấp lỗ hổng cho primitive ghi tồn, (B) đổi `record_movement` sang atomic upsert, (C) partition bảng ledger `stock_movements` theo tháng.

## 1. Bối cảnh & động lực

Tầng kho hiện tại đã đúng chuẩn ERP: ledger bất biến (`StockMovement`, append-only) + số dư materialized (`StockItem`), ghi qua primitive `StockCommandService.record_movement` với `select_for_update` trong `@transaction.atomic`. Không cần đổi công nghệ (Django + Celery đủ). Ba việc dưới đây là hardening chủ động trước khi scale, theo thứ tự rủi ро tăng dần.

Fact nền đã xác minh:
- **Django 6.0.3** — hỗ trợ `CompositePrimaryKey` (từ 5.2).
- **Không có FK nào trỏ vào `stock_movements`** — leaf ledger, đổi PK an toàn.
- **`StockItem`** có unique constraint `uniq_stock_item_wh_prod_state` trên `(warehouse, product, state)` — target cho `ON CONFLICT`.
- Test hiện có: `test_return_api.py` cover return API (chỉ `warranty`); `test_stock_document_approval.py` cover document approval. **`record_movement` chưa có test trực tiếp; chưa có test concurrency.**

## 2. Workstream A — Test lưới an toàn (làm trước, TDD)

Mục tiêu: chốt hành vi hiện tại của primitive trước khi refactor B, và lấp mapping còn thiếu.

### A1. `tests/test_stock_command_service.py` (mới)
- Lần đầu ghi → `get_or_create` tạo `StockItem`; `balance_after == StockItem.quantity`.
- Cộng rồi trừ nhiều lần → số dư tích luỹ đúng.
- `qty_delta == 0` → raise `ValueError`.
- Trừ quá tồn → `went_negative is True`, `balance_after < 0` (oversell có cảnh báo, không chặn).
- **Concurrency (`TransactionTestCase` + threads):** N luồng cùng trừ 1 SKU đồng thời → tổng cuối đúng, không lost update. Chứng minh `select_for_update` hoạt động.

### A2. Bổ sung `tests/test_return_api.py`
- Parametrize cả 4 `return_type` → đúng `StockState` sau khi post:
  - `delivery_returned` → `sellable`
  - `external_return` → `sellable`
  - `warranty` → `warranty`
  - `defect` → `defect`

**Định nghĩa xong:** toàn bộ test A xanh trên codebase hiện tại (chưa refactor B).

## 3. Workstream B — Atomic upsert cho `record_movement`

Thay khối `get_or_create` + `select_for_update().get()` + `item.save()` bằng một upsert Postgres, giữ nguyên chữ ký và `MovementResult`:

```sql
INSERT INTO stock_items (warehouse_id, product_id, state, quantity, updated_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (warehouse_id, product_id, state)
DO UPDATE SET quantity = stock_items.quantity + EXCLUDED.quantity,
              updated_at = now()
RETURNING quantity;
```

- Vẫn trong `@transaction.atomic`.
- `new_balance` lấy từ `RETURNING quantity`.
- `StockMovement` (append-only) tạo sau khi có `new_balance`, `balance_after = new_balance`.
- `went_negative = new_balance < 0`.
- Lợi ích: bớt 1 round-trip DB, thu hẹp cửa sổ giữ lock, loại bỏ race của `get_or_create`.
- **Ràng buộc:** chỉ chạy trên PostgreSQL (dự án đã dùng PG). Test A phải xanh nguyên vẹn — đây là bằng chứng refactor không đổi hành vi.

## 4. Workstream C — Partition `stock_movements` theo tháng

### 4.1. Chiến lược
RANGE partition trên `created_at`, granularity tháng. Django không migrate sang partitioned table tự động → dùng `migrations.SeparateDatabaseAndState`:
- `state_operations`: **rỗng** — model Django giữ PK `id` như cũ, không ripple ra serializer/ORM/admin.
- `database_operations`: `RunSQL` (có `reverse_sql`) làm swap.

Lý do chọn `SeparateDatabaseAndState` thay vì `CompositePrimaryKey` native: giảm blast radius. Model không đổi → mọi chỗ dùng `.pk`/`get(pk=)`/serializer giữ nguyên. `id` vẫn unique toàn cục qua sequence; đọc lọc theo `created_at` vẫn được partition pruning, đọc thuần theo `id` vẫn đúng (chỉ không prune).

### 4.2. Các bước `RunSQL` (forward)
1. `ALTER TABLE stock_movements RENAME TO stock_movements_old;`
2. `CREATE TABLE stock_movements (<cùng cột>) PARTITION BY RANGE (created_at);` với **PK `(id, created_at)`**, `id` gắn sequence hiện tại.
3. Tạo partitions phủ **toàn bộ range dữ liệu cũ** + tháng hiện tại + tháng kế; kèm partition `DEFAULT` chống rớt insert.
4. `INSERT INTO stock_movements SELECT * FROM stock_movements_old;`
5. `SELECT setval(...)` chỉnh sequence về max id.
6. Tạo lại `stockmove_wh_prod_idx`, `stockmove_ref_idx` trên bảng cha (PG propagate xuống partition).
7. `DROP TABLE stock_movements_old;`

`reverse_sql`: swap ngược — rename cha thành `_partitioned`, dựng lại bảng phẳng từ dữ liệu, đổi tên về `stock_movements`.

### 4.3. Tự tạo partition tháng mới
Task Celery beat (đầu mỗi tháng): `CREATE TABLE IF NOT EXISTS stock_movements_YYYYMM PARTITION OF stock_movements FOR VALUES FROM (...) TO (...);` cho tháng N+1. Idempotent. Partition `DEFAULT` là lưới an toàn nếu task lỡ.

### 4.4. An toàn prod
- Toàn bộ swap trong 1 transaction; bảng hiện tại nhỏ → khoá dưới giây.
- **Backup `stock_movements` trước khi chạy trên prod** (dump ra file).
- Runbook migration kèm lệnh rollback.

## 5. Kiểm thử & nghiệm thu

- Chạy bằng `desi-order-web-test` image trên network `desi-order_desi-net` (redis cần password) theo ghi chú `running-backend-tests`.
- Xanh toàn bộ: `apps/inventory/tests/*` + `apps/orders/tests/test_dispatch_stock.py` (đụng cùng movement).
- Sau C: thêm test xác nhận insert route đúng partition theo tháng và query theo khoảng thời gian trả đúng.

## 6. Rủi ро & giảm thiểu

| Rủi ро | Giảm thiểu |
|--------|-----------|
| Upsert đổi hành vi ngầm | Test A chốt trước, phải xanh sau refactor |
| Partition swap hỏng trên prod | Transaction đơn + backup + `reverse_sql` + runbook |
| Rớt insert do thiếu partition tương lai | Partition `DEFAULT` + Celery beat tạo trước tháng |
| Đọc theo `id` thuần không prune | Chấp nhận (hiếm); đọc nghiệp vụ đều theo `created_at`/filter |

## 7. Ngoài phạm vi (YAGNI)
- Không đổi mô hình `StockItem`/`StockMovement` ngoài PK của bảng partition.
- Không refactor tầng transport/serializer.
- Không partition các bảng khác (`stock_documents`, `stock_return_notes`) — chưa cần.
