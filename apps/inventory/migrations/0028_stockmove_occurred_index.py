from django.db import migrations, models


class Migration(migrations.Migration):
    """Index cho cột ngày nghiệp vụ — tách riêng để không gộp khoá với 0026.

    CREATE INDEX CONCURRENTLY không dùng được trên bảng partition, nên index này
    tạo thường: nó đệ quy xuống TỪNG partition tháng và giữ khoá ghi suốt lúc
    build. Gộp chung transaction với UPDATE + SET NOT NULL của 0026 thì cả sổ kho
    bị ACCESS EXCLUSIVE một lượt dài. Đứng riêng + `atomic = False` thì đây là
    migration duy nhất cần canh giờ thấp điểm.

    Chỉ khai báo index ở ĐÚNG MỘT migration (ở đây) — `Meta.indexes` của model
    giữ nguyên, `makemigrations --check` vì thế không thấy drift.
    """

    atomic = False

    dependencies = [("inventory", "0027_backfill_occurred_at_from_scans")]

    operations = [
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(
                fields=["reference_type", "movement_type", "occurred_at"],
                name="stockmove_reftype_occurred_idx",
            ),
        ),
    ]
