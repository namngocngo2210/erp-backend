import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Bảng snapshot mốc vòng đời đơn (spec 2026-08-04-order-milestone-snapshots)."""

    dependencies = [
        ("orders", "0114_order_landing_confirmed_amount"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderMilestoneSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("milestone", models.CharField(choices=[("created", "Đơn đổ về"), ("invoiced", "Đã tạo hóa đơn")], max_length=20)),
                ("total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total_after_discount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("quantity", models.PositiveIntegerField(default=0)),
                ("sku", models.TextField(blank=True, default="")),
                ("amount_source", models.CharField(blank=True, default="", max_length=20)),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milestone_snapshots",
                        to="orders.order",
                    ),
                ),
            ],
            options={"db_table": "order_milestone_snapshots"},
        ),
        migrations.AddConstraint(
            model_name="ordermilestonesnapshot",
            constraint=models.UniqueConstraint(fields=("order", "milestone"), name="uniq_order_milestone"),
        ),
    ]
