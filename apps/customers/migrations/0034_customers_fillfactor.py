"""Chừa 15% chỗ trống mỗi page của `customers` để HOT update hoạt động trở lại.

Đo trên prod 2026-08-17:

    customers — heap 128MB, index 432MB (3,4× heap!), 13 index, 756.409 dòng
    n_tup_upd = 16.672.973   n_tup_hot_upd = 316.233   → HOT chỉ 1,9%

So sánh với `customer_segment_snapshot` trên cùng database: HOT 99,9%.

HOT (Heap-Only Tuple) là cơ chế cho phép UPDATE ghi bản mới NGAY TRONG page cũ và
KHÔNG phải cập nhật một index nào. Nó chỉ áp dụng khi đủ hai điều kiện: (a) không
cột nào bị index thay đổi, và (b) page còn chỗ trống cho bản ghi mới.

`customers` để `fillfactor` mặc định = 100, tức Postgres nhồi đầy page ngay từ đầu
và không chừa chỗ nào cho điều kiện (b). Hệ quả: ~16,3 triệu update phải ghi bản
mới sang page khác và cập nhật CẢ 13 index — đó là lý do bảng có 432MB index trên
128MB heap, và là nguồn write amplification lớn nhất còn lại của hệ thống.

`fillfactor = 85` là mức thường dùng cho bảng update nặng: đổi ~15% dung lượng
heap (128MB → ~150MB) lấy cơ hội HOT trên phần lớn update.

⚠️ Migration này CHỈ đổi tham số cho các page GHI TỪ ĐÂY TRỞ ĐI. Các page đã đầy
sẵn không tự giãn ra — muốn áp ngay cho dữ liệu cũ phải viết lại bảng bằng
`pg_repack` (không khoá) hoặc `VACUUM FULL` (khoá bảng). Xem
`docs/runbooks/2026-08-17-db-maintenance-repack-analyze.md`.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0033_incremental_segment_snapshot_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE customers SET (fillfactor = 85);",
            reverse_sql="ALTER TABLE customers RESET (fillfactor);",
        ),
        # `customer_segment_snapshot` bị bỏ quên ở đợt tune 0032 (chỉ customers
        # và orders được hạ xuống 0.05) — trong khi nó là bảng bị ghi lại nhiều
        # nhất hệ thống. Với scale_factor mặc định 0.2 trên 770k dòng, autovacuum
        # chỉ chạm tới sau khi tích đủ ~154k dead tuple; lần full rebuild hằng
        # ngày sinh nhiều hơn thế trong một lượt, nên bảng luôn ở trạng thái
        # phồng (đo được 523MB heap cho khoảng 81MB dữ liệu thật — 6,4×).
        migrations.RunSQL(
            sql="""
            ALTER TABLE customer_segment_snapshot SET (
                autovacuum_vacuum_scale_factor = 0.05,
                autovacuum_analyze_scale_factor = 0.05
            );
            """,
            reverse_sql="""
            ALTER TABLE customer_segment_snapshot RESET (
                autovacuum_vacuum_scale_factor,
                autovacuum_analyze_scale_factor
            );
            """,
        ),
    ]
