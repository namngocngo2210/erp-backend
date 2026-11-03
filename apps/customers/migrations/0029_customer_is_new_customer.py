from django.db import migrations, models


# Backfill: set False cho khách đã có ≥1 đơn GIAO THÀNH CÔNG (default True phủ phần
# còn lại). Đơn tính = chưa xóa mềm, status=3 (DELIVERED_SUCCESS), hóa đơn không bị hủy
# (invoice_status != 2) — cùng rule với apps.customers.customer_type.
BACKFILL_SQL = """
UPDATE customers
SET is_new_customer = FALSE
WHERE id IN (
    SELECT DISTINCT customer_id
    FROM orders
    WHERE deleted_at IS NULL
      AND status = 3
      AND (invoice_status IS NULL OR invoice_status <> 2)
)
"""


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0028_alter_customer_code"),
        ("orders", "0105_order_reattached_invoice_codes"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="is_new_customer",
            field=models.BooleanField(
                default=True,
                help_text="True: khách mới (chưa có đơn giao thành công), False: khách cũ (≥1 đơn giao thành công)",
            ),
        ),
        migrations.RunSQL(BACKFILL_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
