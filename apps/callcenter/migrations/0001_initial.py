from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("orders", "0106_floating_pool"),
    ]

    operations = [
        migrations.CreateModel(
            name="CallRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("call_id", models.CharField(max_length=100, unique=True)),
                ("call_type", models.CharField(blank=True, default="", max_length=5)),
                ("agent_id", models.CharField(blank=True, default="", max_length=100)),
                ("agent_name", models.CharField(blank=True, default="", max_length=255)),
                ("caller", models.CharField(blank=True, default="", max_length=30)),
                ("caller_normalized", models.CharField(blank=True, default="", max_length=20)),
                ("called", models.CharField(blank=True, default="", max_length=30)),
                ("recording_url", models.CharField(blank=True, default="", max_length=1024)),
                ("start_time", models.DateTimeField(blank=True, null=True)),
                ("end_time", models.DateTimeField(blank=True, null=True)),
                ("acd_time", models.PositiveIntegerField(default=0)),
                ("matched_at", models.DateTimeField(blank=True, null=True)),
                ("match_attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "transcribe_status",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (0, "Chờ transcribe"),
                            (1, "Đang transcribe"),
                            (2, "Hoàn thành"),
                            (3, "Thất bại"),
                            (4, "Bỏ qua"),
                        ],
                        default=0,
                    ),
                ),
                ("transcript", models.TextField(blank=True, default="")),
                ("transcribe_error", models.TextField(blank=True, default="")),
                ("transcribe_attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="call_records",
                        to="orders.order",
                    ),
                ),
            ],
            options={"db_table": "callcenter_call_records"},
        ),
        migrations.AddIndex(
            model_name="callrecord",
            index=models.Index(fields=["transcribe_status", "id"], name="callrec_status_id_idx"),
        ),
        migrations.AddIndex(
            model_name="callrecord",
            index=models.Index(fields=["order", "start_time"], name="callrec_order_start_idx"),
        ),
        migrations.AddIndex(
            model_name="callrecord",
            index=models.Index(
                condition=Q(order__isnull=True),
                fields=["caller_normalized"],
                name="callrec_unmatched_phone_idx",
            ),
        ),
    ]
