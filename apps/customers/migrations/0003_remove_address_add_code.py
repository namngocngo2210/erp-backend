# Generated manually - Remove address, add code field and indexes
from django.db import migrations, models


def populate_customer_codes(apps, schema_editor):
    """Điền code cho các customer đã tồn tại dựa trên created_at"""
    Customer = apps.get_model('customers', 'Customer')

    for customer in Customer.objects.filter(code__isnull=True).iterator():
        # Tạo code dựa trên created_at
        if customer.created_at:
            timestamp = customer.created_at.strftime("%Y%m%d%H%M%S%f")
        else:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        code = f"CUST-{timestamp}"
        customer.code = code
        customer.save(update_fields=['code'])


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_add_query_indexes'),
    ]

    operations = [
        # Xóa index _like cũ nếu tồn tại (trước khi thêm field mới)
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS customers_customer_code_f5e1b9fb_like;",
            reverse_sql=""
        ),

        # Thêm field code cho phép null trước (không có db_index để tránh tạo index mới)
        migrations.AddField(
            model_name='customer',
            name='code',
            field=models.CharField(max_length=50, null=True, blank=True),
        ),

        # Điền code cho các customer đã tồn tại
        migrations.RunPython(populate_customer_codes, migrations.RunPython.noop),

        # Đặt unique constraint sau khi đã có dữ liệu (unique tự tạo index)
        migrations.AlterField(
            model_name='customer',
            name='code',
            field=models.CharField(max_length=50, unique=True),
        ),

        # Xóa field address
        migrations.RemoveField(
            model_name='customer',
            name='address',
        ),

        # Thay đổi phone_normalized không còn unique
        migrations.AlterField(
            model_name='customer',
            name='phone_normalized',
            field=models.CharField(max_length=30, blank=True, default=''),
        ),

        # Thêm composite index (code, phone_normalized)
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['code', 'phone_normalized'], name='customer_code_phone_idx'),
        ),

        # Thêm composite index (code, phone_raw)
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['code', 'phone_raw'], name='customer_code_phone_raw_idx'),
        ),
    ]
