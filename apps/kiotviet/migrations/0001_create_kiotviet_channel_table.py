from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='KiotVietChannel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Channel Name')),
                ('branch_id', models.CharField(max_length=100, verbose_name='Branch ID')),
                ('branch_ids', ArrayField(models.TextField(), blank=True, default=list, verbose_name='Branch IDs')),
                ('client_id', models.CharField(max_length=255, verbose_name='Client ID')),
                ('client_secret', models.CharField(max_length=255, verbose_name='Client Secret')),
                ('retailer', models.CharField(max_length=255, verbose_name='Retailer')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
            ],
            options={
                'verbose_name': 'KiotViet Channel',
                'verbose_name_plural': 'KiotViet Channels',
                'db_table': 'kiotviet_kiotvietchannel',
            },
        ),
    ]
