from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warranty_codes', '0003_activation_and_scan_log'),
    ]

    operations = [
        migrations.AlterField(
            model_name='warrantyactivation',
            name='phone',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
