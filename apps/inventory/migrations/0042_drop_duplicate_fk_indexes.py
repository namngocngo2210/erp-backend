"""Bỏ 3 index khai lại đúng cột FK — trùng tuyệt đối với index Django tự sinh
(audit 2026-09-23 T2/P2-3): stock_return_entries.returnentry_note_idx /
returnentry_order_idx, cancelled_dispatch_watches.cancelwatch_wh_idx. Bảng nhỏ
(≤72kB) → RemoveIndex thường."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0041_stock_flow_note"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="returnentry", name="returnentry_note_idx"),
        migrations.RemoveIndex(model_name="returnentry", name="returnentry_order_idx"),
        migrations.RemoveIndex(model_name="cancelleddispatchwatch", name="cancelwatch_wh_idx"),
    ]
