"""Bảng cho chiều NGƯỢC CNV → desi-order (spec 2026-08-19).

Toàn bộ là CREATE TABLE mới, không đụng bảng đang chạy → chạy được giờ hành
chính. Đổi `OrderSource.choices` nằm ở migration bên `orders`.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cnv_loyalty", "0002_cnvconnection_account_id"),
        ("orders", "0129_remove_legacy_recall_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CnvInboundCursor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource", models.CharField(max_length=64, unique=True)),
                ("synced_until", models.DateTimeField()),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
            ],
            options={"db_table": "cnv_inbound_cursors"},
        ),
        migrations.CreateModel(
            name="CnvWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("topic", models.CharField(blank=True, default="", max_length=64)),
                ("cnv_object_id", models.BigIntegerField(blank=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("payload_hash", models.CharField(max_length=64)),
                ("processed", models.BooleanField(default=False)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "cnv_webhook_events"},
        ),
        migrations.CreateModel(
            name="CnvOrderLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cnv_order_id", models.BigIntegerField(unique=True)),
                ("cnv_name", models.CharField(blank=True, default="", max_length=100)),
                ("unique_id", models.CharField(blank=True, default="", max_length=128)),
                ("cnv_customer_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("financial_status", models.CharField(blank=True, default="", max_length=32)),
                ("fulfillment_status", models.CharField(blank=True, default="", max_length=32)),
                ("cancelled_status", models.CharField(blank=True, default="", max_length=32)),
                ("location_id", models.BigIntegerField(blank=True, null=True)),
                ("subtotal_price", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("total_discounts", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("total_price", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("shipment_fee", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("cnv_created_at", models.DateTimeField(blank=True, null=True)),
                ("cnv_updated_at", models.DateTimeField(blank=True, null=True)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cnv_link",
                        to="orders.order",
                    ),
                ),
            ],
            options={"db_table": "cnv_order_links"},
        ),
        migrations.CreateModel(
            name="CnvOrderLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_index", models.PositiveIntegerField()),
                ("cnv_product_id", models.BigIntegerField(blank=True, null=True)),
                ("cnv_variant_id", models.BigIntegerField(blank=True, null=True)),
                ("sku", models.CharField(blank=True, default="", max_length=100)),
                ("product_name", models.CharField(blank=True, default="", max_length=255)),
                ("quantity", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("unit_price", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("total_discount", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="cnv_loyalty.cnvorderlink",
                    ),
                ),
            ],
            options={"db_table": "cnv_order_lines"},
        ),
        migrations.AddIndex(
            model_name="cnvorderlink",
            index=models.Index(fields=["cnv_updated_at"], name="cnv_link_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="cnvorderline",
            index=models.Index(fields=["sku"], name="cnv_order_line_sku_idx"),
        ),
        migrations.AddConstraint(
            model_name="cnvorderline",
            constraint=models.UniqueConstraint(
                fields=("link", "line_index"), name="cnv_order_line_idx_uniq"
            ),
        ),
        migrations.AddIndex(
            model_name="cnvwebhookevent",
            index=models.Index(
                condition=models.Q(("processed", False)),
                fields=["received_at"],
                name="cnv_webhook_unprocessed_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cnvwebhookevent",
            index=models.Index(fields=["received_at"], name="cnv_webhook_received_idx"),
        ),
        migrations.AddConstraint(
            model_name="cnvwebhookevent",
            constraint=models.UniqueConstraint(
                fields=("topic", "cnv_object_id", "payload_hash"),
                name="cnv_webhook_event_uniq",
            ),
        ),
    ]
