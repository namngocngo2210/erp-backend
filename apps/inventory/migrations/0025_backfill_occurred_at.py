from django.db import migrations

_BATCH = 50_000


def backfill(apps, schema_editor):
    """occurred_at = created_at cho dữ liệu cũ, chia lô theo id.

    `atomic = False` bên dưới là bắt buộc: nếu cả migration nằm trong 1
    transaction thì chia lô vô nghĩa (khoá giữ tới cuối). Mỗi UPDATE tự commit.
    Chạy lại được: điều kiện `occurred_at IS NULL` khiến lô đã xong thành no-op.
    """
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), -1) FROM stock_movements;")
        low, high = cur.fetchone()
        while low <= high:
            cur.execute(
                "UPDATE stock_movements SET occurred_at = created_at "
                "WHERE id >= %s AND id < %s AND occurred_at IS NULL;",
                [low, low + _BATCH],
            )
            low += _BATCH


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("inventory", "0024_stockmovement_occurred_at")]

    operations = [
        # Chiều ngược: cột sẽ bị 0024 xoá nguyên vẹn, không cần undo dữ liệu.
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
