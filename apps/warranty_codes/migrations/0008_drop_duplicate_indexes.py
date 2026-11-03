"""Bỏ 3 index trùng tuyệt đối trên warranty_codes (audit 2026-09-23 T2/P2-3):
- wc_code_batch_idx ≡ warranty_codes_batch_id_* (FK auto, 704kB),
- wc_code_like_idx ≡ warranty_codes_code_*_like (3,1MB) — `unique=True` trên
  varchar đã làm Django tự sinh index varchar_pattern_ops,
- wc_serial_like_idx ≡ warranty_codes_serial_*_like (3,1MB) — như trên.

Bảng bị quét/ghi bởi portal QR công khai → DROP INDEX CONCURRENTLY, atomic=False.
"""

from django.db import migrations


def _drop_concurrently(name: str, column_def: str):
    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunSQL(
                sql=f"DROP INDEX CONCURRENTLY IF EXISTS {name}",
                reverse_sql=(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                    f"ON warranty_codes ({column_def})"
                ),
            ),
        ],
        state_operations=[migrations.RemoveIndex(model_name="warrantycode", name=name)],
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("warranty_codes", "0007_batch_generated_seq"),
    ]

    operations = [
        _drop_concurrently("wc_code_batch_idx", "batch_id"),
        _drop_concurrently("wc_code_like_idx", "code varchar_pattern_ops"),
        _drop_concurrently("wc_serial_like_idx", "serial varchar_pattern_ops"),
    ]
