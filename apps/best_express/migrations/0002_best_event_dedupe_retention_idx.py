"""Dedupe + retention cho best_webhook_events (audit 2026-07-14).

Bảng còn rỗng (app chưa wire vào luồng đơn) nên AddIndex/AddConstraint thường là đủ —
không cần CONCURRENTLY. Nếu về sau bảng đã lớn thì phải chuyển sang CONCURRENTLY.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('best_express', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='bestwebhookevent',
            index=models.Index(fields=['received_at'], name='best_event_received_idx'),
        ),
        migrations.AddIndex(
            model_name='bestwebhookevent',
            index=models.Index(condition=models.Q(('processed', False)), fields=['processed', 'received_at'], name='best_event_unprocessed_idx'),
        ),
        migrations.AddConstraint(
            model_name='bestwebhookevent',
            constraint=models.UniqueConstraint(condition=models.Q(('code_best', ''), ('merchant_order_code__gt', ''), ('modified_at_ms__gt', 0)), fields=('merchant_order_code', 'best_status_code', 'modified_at_ms'), name='best_webhook_event_merchant_uniq'),
        ),
    ]
