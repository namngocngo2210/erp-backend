from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0021_channel_ad_accounts"),
        ("orders", "0112_price_books"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="price_book",
            field=models.ForeignKey(
                blank=True,
                help_text="Bảng giá áp dụng cho đơn của kênh này. Rỗng = dùng bảng giá mặc định.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="channels",
                to="orders.pricebook",
            ),
        ),
    ]
