import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0001_initial"),
        ("warehouses", "0001_initial"),
        ("inventory", "0008_partition_stock_movements"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockReclassLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "from_state",
                    models.CharField(
                        choices=[("sellable", "Bán được"), ("warranty", "Bảo hành"), ("defect", "Lỗi")],
                        max_length=20,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("to_sellable", "Chuyển về bán được"),
                            ("send_back", "Gửi lại khách"),
                            ("scrap", "Huỷ hàng lỗi"),
                        ],
                        max_length=20,
                    ),
                ),
                ("quantity", models.PositiveIntegerField()),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reclass_logs",
                        to="orders.product",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reclass_logs",
                        to="warehouses.warehouse",
                    ),
                ),
            ],
            options={
                "db_table": "stock_reclass_logs",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="stockreclasslog",
            index=models.Index(fields=["warehouse", "product", "created_at"], name="reclasslog_wh_prod_idx"),
        ),
    ]
