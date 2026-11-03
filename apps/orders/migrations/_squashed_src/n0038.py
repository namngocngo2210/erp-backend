# Verbatim copy of apps/orders/migrations/0038_orderassessment_extended_crm_fields.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


def add_shoulder_blade_need_type(apps, schema_editor):
    NeedGroup = apps.get_model("orders", "NeedGroup")
    NeedType = apps.get_model("orders", "NeedType")
    group = NeedGroup.objects.filter(code="pain_relief").first()
    if not group:
        return
    NeedType.objects.update_or_create(
        code="shoulder_blade",
        defaults={
            "group": group,
            "name_vi": "đau bả vai",
            "sort_order": 35,
            "is_active": True,
        },
    )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0037_add_cancel_invoice_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderassessment",
            name="age_text",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="occupation_code",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="delax_source_codes",
            field=ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="pain_level_code",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="pain_duration_code",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="lifestyle_habit_codes",
            field=ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="used_massage_before",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="relax_time_codes",
            field=ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="purchase_barrier_codes",
            field=ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="comparing_brand_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="call_duration_code",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="post_purchase_feedback_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="orderassessment",
            name="post_purchase_rating_code",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.RunPython(add_shoulder_blade_need_type, noop_reverse),
    ]
