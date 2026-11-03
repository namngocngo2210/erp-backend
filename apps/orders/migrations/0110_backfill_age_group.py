"""Backfill age_text tự do → mã nhóm tuổi.

Import parser từ app thay vì copy vào migration: hàm này còn được
`import_assessment_csv` dùng lâu dài nên sẽ không biến mất, và migration chỉ
đụng dòng đã tồn tại — DB mới không có dòng nào để chạy sai.
"""
from django.db import migrations

from apps.orders.age_group_backfill import parse_age_group

BATCH_SIZE = 1000


def backfill(apps, schema_editor):
    OrderAssessment = apps.get_model("orders", "OrderAssessment")
    pending = []
    # PERFORMANCE guard, không phải correctness guard: parse_age_group("")
    # đã trả về "" nên bỏ qua dòng rỗng không đổi kết quả, chỉ để khỏi quét
    # ~476,000 dòng age_text rỗng chưa từng được hỏi.
    queryset = OrderAssessment.objects.exclude(age_text="").only("id", "age_text")
    for row in queryset.iterator(chunk_size=BATCH_SIZE):
        converted = parse_age_group(row.age_text)
        if converted != row.age_text:
            row.age_text = converted
            pending.append(row)
    for start in range(0, len(pending), BATCH_SIZE):
        OrderAssessment.objects.bulk_update(pending[start:start + BATCH_SIZE], ["age_text"])


class Migration(migrations.Migration):
    dependencies = [("orders", "0109_drop_dead_indexes")]

    # Không có reverse: giá trị gốc không suy ngược được từ mã nhóm. Lưới an
    # toàn là bản dump CSV chạy trước khi deploy (xem spec, mục Vận hành).
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
