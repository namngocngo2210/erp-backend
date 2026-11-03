"""Vá lại FK tầng DB cho bảng partitioned `stock_movements`.

Migration 0008 dựng lại bảng bằng raw SQL và làm rơi toàn bộ mệnh đề REFERENCES
— PROTECT/SET_NULL của Django từ đó chỉ được thực thi ở tầng ORM. Postgres hỗ
trợ FK OUTGOING trên bảng partitioned (chỉ FK trỏ VÀO bảng partitioned mới bị
giới hạn trước PG12) nên thêm lại được.

Postgres CHƯA hỗ trợ `NOT VALID` cho FK trên bảng partitioned (đã thử —
WrongObjectType) nên phải ADD thẳng: constraint validate ngay lúc thêm, giữ
SHARE ROW EXCLUSIVE trên cả 2 bảng trong lúc scan. `atomic = False` để mỗi
statement tự commit, giữ cửa sổ khoá tối thiểu.

Lưu ý vận hành: chạy migrate này GIỜ THẤP ĐIỂM (scan toàn bộ stock_movements).
Nếu ADD fail nghĩa là dữ liệu ĐANG có movement mồ côi — xử lý dữ liệu trước
rồi migrate lại, không được bỏ constraint.
"""
from django.db import migrations

_FKS = [
    (
        "stockmove_warehouse_fk",
        "FOREIGN KEY (warehouse_id) REFERENCES warehouses (id) ON DELETE RESTRICT",
    ),
    (
        "stockmove_product_fk",
        "FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT",
    ),
    (
        "stockmove_created_by_fk",
        "FOREIGN KEY (created_by_id) REFERENCES users (id) ON DELETE SET NULL",
    ),
]

class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("inventory", "0031_pendingscan_claimed_at"),
        # Bảng đích phải tồn tại trước khi trỏ FK.
        ("warehouses", "0001_initial"),
        ("orders", "0001_initial"),
        ("users", "0001_initial"),
    ]

    operations = (
        [
            # Idempotent: DROP trước cho môi trường đã lỡ thêm tay.
            migrations.RunSQL(
                sql=(
                    f"ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS {name};\n"
                    f"ALTER TABLE stock_movements ADD CONSTRAINT {name} {clause};"
                ),
                reverse_sql=(
                    f"ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS {name};"
                ),
            )
            for name, clause in _FKS
        ]
        + [
            # Index FK created_by cũng rơi cùng 0008 (2 cột kia đã có index nghiệp vụ).
            migrations.RunSQL(
                sql=(
                    "CREATE INDEX IF NOT EXISTS stockmove_created_by_idx "
                    "ON stock_movements (created_by_id);"
                ),
                reverse_sql="DROP INDEX IF EXISTS stockmove_created_by_idx;",
            )
        ]
    )
