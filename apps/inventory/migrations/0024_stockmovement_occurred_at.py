from django.db import migrations, models


class Migration(migrations.Migration):
    """Thêm cột nullable — thao tác metadata, KHÔNG rewrite bảng partition.

    Backfill và NOT NULL nằm ở 0025/0026 để mỗi migration giữ khoá ngắn.
    """

    dependencies = [("inventory", "0023_returnnoteline_quantity_positive")]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="occurred_at",
            field=models.DateTimeField(null=True),
        ),
        # DEFAULT ở TẦNG DB, không phải chỉ ở Python. Bắt buộc, vì cửa sổ deploy
        # KHÔNG nguyên tử: `scripts/deploy-prod.sh` chạy migrate từ container web
        # trong khi worker/beat CŨ vẫn đang chạy (riêng worker-kiotviet còn không
        # được recreate). Code cũ không biết cột này nên INSERT bỏ trống nó:
        #   - không có default DB ⇒ occurred_at = NULL, rồi 0026 sẽ dập cho tất cả
        #     các dòng đó một mốc thời gian LÚC CHẠY MIGRATION > created_at của
        #     chúng ⇒ vỡ bất biến occurred_at <= created_at ⇒
        #     `movement_window.filter_occurred_between` loại các dòng này khỏi
        #     MỌI khung ngày (biến mất khỏi tab Xuất/thẻ tổng/export, không báo lỗi).
        #   - có default now() ⇒ occurred_at ≈ created_at ngay lúc ghi: bất biến
        #     giữ nguyên và giá trị cũng đúng nghiệp vụ.
        # Chiều ngược là noop: 0024 rollback sẽ xoá luôn cả cột lẫn default.
        migrations.RunSQL(
            "ALTER TABLE stock_movements ALTER COLUMN occurred_at SET DEFAULT now();",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
