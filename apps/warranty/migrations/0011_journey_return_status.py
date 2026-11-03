# Generated for §7 return leg delivery status.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warranty', '0010_journey_statuses_warehouse_check_drop_kv'),
    ]

    operations = [
        migrations.AddField(
            model_name='warrantyjourney',
            name='return_status',
            field=models.CharField(blank=True, choices=[('shipping', 'Đang giao'), ('delivered', 'GTC')], default='', max_length=10),
        ),
    ]
