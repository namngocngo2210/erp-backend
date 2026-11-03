import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Chốt NOT NULL sau khi 0025 đã lấp hết, và giữ lại DEFAULT now() ở tầng DB.

    `atomic = False`: nếu cả migration nằm trong MỘT transaction thì lượt UPDATE
    quét sổ + lượt validate SET NOT NULL đều giữ ACCESS EXCLUSIVE trên bảng
    partition cha (và đệ quy xuống mọi partition tháng) tới tận commit. Tách ra
    thì khoá được giữ theo từng mảnh ngắn. Index đã dời sang 0028 vì
    CREATE INDEX không CONCURRENTLY được trên bảng partition — nó là phần giữ
    khoá lâu nhất, không được gộp chung ở đây.
    """

    atomic = False

    dependencies = [("inventory", "0025_backfill_occurred_at")]

    operations = [
        # Vét nốt dòng còn NULL do code CŨ ghi trong cửa sổ giữa 0024 và 0026
        # (trước khi default DB kịp có, hoặc đường ghi raw-SQL nào đó bỏ cột).
        # PHẢI tự làm trước AlterField: UPDATE do Django tự sinh dập một HẰNG SỐ
        # thời gian ĐÓNG BĂNG lúc migrate — luôn LỚN HƠN created_at của các dòng
        # cũ đó ⇒ vỡ bất biến occurred_at <= created_at. Lấy chính created_at của
        # dòng mới là giá trị đúng. Sau câu này, UPDATE của Django khớp 0 dòng.
        migrations.RunSQL(
            "UPDATE stock_movements SET occurred_at = created_at WHERE occurred_at IS NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="occurred_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        # Đặt lại DEFAULT now() — BẮT BUỘC là thao tác CUỐI CÙNG.
        # AlterField ở trên tự sinh chuỗi: SET DEFAULT '<hằng số đóng băng>' →
        # UPDATE → SET NOT NULL → **DROP DEFAULT**. Tức là Django XOÁ mất default
        # mà 0024 đã đặt. Nếu để nguyên, mọi INSERT không nêu tên cột (code cũ còn
        # sống trong cửa sổ deploy, hoặc bất kỳ đường ghi raw-SQL nào) sẽ nổ
        # NOT NULL violation. `default=timezone.now` của model chỉ tồn tại ở tầng
        # Python — nó KHÔNG bảo vệ được các đường ghi đó. Giữ cả hai tầng.
        migrations.RunSQL(
            "ALTER TABLE stock_movements ALTER COLUMN occurred_at SET DEFAULT now();",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
