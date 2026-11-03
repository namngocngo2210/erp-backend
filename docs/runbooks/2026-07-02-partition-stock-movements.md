# Runbook: Partition stock_movements (migration inventory.0008)

## Trước khi chạy
1. Backup bảng: `pg_dump -t stock_movements -Fc $DB > stock_movements_backup_$(date +%F).dump`
2. Kiểm tra kích thước: `SELECT count(*), min(created_at), max(created_at) FROM stock_movements;`
3. Chọn cửa sổ ít tải (swap nằm trong 1 transaction; bảng nhỏ → dưới giây, nhưng khoá bảng trong lúc INSERT copy).

## Chạy
- `python manage.py migrate inventory 0008`

## Kiểm tra sau
- `SELECT partstrat FROM pg_partitioned_table pt JOIN pg_class c ON c.oid=pt.partrelid WHERE c.relname='stock_movements';` → `r`
- Đếm dòng khớp backup: `SELECT count(*) FROM stock_movements;`
- Xác nhận có partition `stock_movements_default`, `stock_movements_hist` (nếu có dữ liệu cũ trước tháng hiện tại), và partition tháng hiện tại + tháng kế `stock_movements_YYYYMM`.
- Ghi 1 movement thử (receipt nội bộ) → đọc lại đúng.

## Rollback
- `python manage.py migrate inventory 0007` (chạy REVERSE_SQL dựng lại bảng phẳng).
- Nếu lỗi giữa chừng: restore từ dump: `pg_restore -t stock_movements -d $DB --clean stock_movements_backup_*.dump`

## Sau triển khai
- Xác nhận beat job `inventory-ensure-stock-partition` xuất hiện ở trang cron admin (chạy ngày 25 hàng tháng, tạo trước partition tháng kế).
- Chạy tay 1 lần để xác nhận: `celery -A config call apps.inventory.tasks.ensure_stock_movements_partition` (hoặc trong `manage.py shell`: `from apps.inventory.tasks import ensure_stock_movements_partition; ensure_stock_movements_partition.run()`). Vì migration đã tạo sẵn partition tháng hiện tại/kế, lần chạy này là no-op (`CREATE TABLE IF NOT EXISTS`).
