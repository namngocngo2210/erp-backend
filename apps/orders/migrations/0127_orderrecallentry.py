import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0126_order_koc_type_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderRecallEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recall_code", models.CharField(blank=True, default="", max_length=64)),
                ("recall_created_date", models.DateField(blank=True, null=True)),
                ("recall_received_date", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recall_entries",
                        to="orders.order",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_order_recall_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "order_recall_entries",
                "ordering": ["id"],
            },
        ),
        migrations.AddIndex(
            model_name="orderrecallentry",
            index=models.Index(fields=["order", "id"], name="recall_entry_order_idx"),
        ),
        migrations.AddIndex(
            model_name="orderrecallentry",
            index=models.Index(fields=["recall_code"], name="recall_entry_code_idx"),
        ),
    ]
