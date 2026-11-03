"""Nhật ký cơ hội resale tạo từ đơn giao thành công (docs/foxia-resale-delivered.md)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("foxia", "0007_link_cells_by_kind"),
        ("orders", "0139_order_sales_closed_at_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FoxiaResalePush",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("delivered_on", models.DateField()),
                ("phone", models.CharField(blank=True, default="", max_length=30)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Đang đẩy"),
                            ("ok", "Thành công"),
                            ("failed", "Thất bại"),
                            ("skipped_no_phone", "Bỏ qua — đơn không có SĐT"),
                            ("skipped_duplicate", "Bỏ qua — SĐT đã có cơ hội resale trong ngày"),
                        ],
                        max_length=32,
                    ),
                ),
                ("foxia_source_id", models.BigIntegerField()),
                ("url", models.CharField(blank=True, default="", max_length=500)),
                ("request_body", models.JSONField(blank=True, null=True)),
                ("http_status", models.IntegerField(blank=True, null=True)),
                ("response_body", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("cohoi_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("khachhang_id", models.BigIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "staff",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="foxia_resale_push",
                        to="orders.order",
                    ),
                ),
            ],
            options={
                "db_table": "foxia_resale_pushes",
                "indexes": [
                    models.Index(fields=["delivered_on", "phone"], name="foxia_resale_day_phone_idx"),
                    models.Index(fields=["status", "-created_at"], name="foxia_resale_status_idx"),
                    models.Index(fields=["created_at"], name="foxia_resale_created_idx"),
                ],
            },
        ),
    ]
