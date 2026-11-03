from django.db import migrations, models


class Migration(migrations.Migration):
    """Snapshot tiền khách tự đăng ký trên landing (spec 2026-08-04).

    ADD COLUMN ... DEFAULT 0 thuần — không rewrite bảng, an toàn giờ hành chính.
    """

    dependencies = [
        ("orders", "0113_seed_dynamic_field_defs"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="landing_confirmed_amount",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, default=0, db_default=0
            ),
        ),
    ]
