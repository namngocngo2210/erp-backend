# Verbatim copy of apps/orders/migrations/0025_orderassessment_crm_models.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


def seed_need_catalog(apps, schema_editor):
    NeedGroup = apps.get_model("orders", "NeedGroup")
    NeedType = apps.get_model("orders", "NeedType")

    group_seed = [
        {"code": "pain_relief", "name_vi": "Giảm đau", "sort_order": 10},
        {"code": "beauty", "name_vi": "Làm đẹp", "sort_order": 20},
        {"code": "wellness", "name_vi": "Sức khỏe", "sort_order": 30},
        {"code": "family", "name_vi": "Gia đình", "sort_order": 40},
    ]
    type_seed = [
        {"code": "neck_shoulder", "group_code": "pain_relief", "name_vi": "Vai gáy", "sort_order": 10},
        {"code": "back_pain", "group_code": "pain_relief", "name_vi": "Đau lưng", "sort_order": 20},
        {"code": "sleep_support", "group_code": "wellness", "name_vi": "Hỗ trợ giấc ngủ", "sort_order": 10},
        {"code": "skin_brightening", "group_code": "beauty", "name_vi": "Sáng da", "sort_order": 10},
        {"code": "anti_aging", "group_code": "beauty", "name_vi": "Chống lão hóa", "sort_order": 20},
        {"code": "gift_for_parent", "group_code": "family", "name_vi": "Mua cho ba mẹ", "sort_order": 10},
    ]

    groups = {}
    for item in group_seed:
        group, _ = NeedGroup.objects.update_or_create(
            code=item["code"],
            defaults={
                "name_vi": item["name_vi"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )
        groups[item["code"]] = group

    for item in type_seed:
        NeedType.objects.update_or_create(
            code=item["code"],
            defaults={
                "group": groups[item["group_code"]],
                "name_vi": item["name_vi"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0024_order_invoice_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NeedGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name_vi", models.CharField(max_length=255)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="OrderJourneyEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("from_code", models.CharField(blank=True, max_length=64, null=True)),
                ("to_code", models.CharField(blank=True, max_length=64, null=True)),
                ("note", models.TextField(blank=True, default="")),
                ("payload_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="journey_events", to="orders.order")),
            ],
        ),
        migrations.CreateModel(
            name="OrderAssessment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_classification_code", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("customer_attitude_raw", models.CharField(blank=True, default="", max_length=255)),
                ("sales_status_code", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("not_closed_reason_code", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("delivery_business_status_code", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("bomb_reason_text", models.TextField(blank=True, default="")),
                ("internal_note", models.TextField(blank=True, default="")),
                (
                    "potential_level",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="assessment", to="orders.order")),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_order_assessments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="NeedType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name_vi", models.CharField(max_length=255)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="need_types", to="orders.needgroup")),
            ],
            options={
                "ordering": ["group__sort_order", "sort_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="OrderNeed",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("need_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_needs", to="orders.needtype")),
                ("order_assessment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="needs", to="orders.orderassessment")),
            ],
        ),
        migrations.AddIndex(
            model_name="needgroup",
            index=models.Index(fields=["is_active", "sort_order"], name="need_group_active_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="needtype",
            index=models.Index(fields=["group", "is_active", "sort_order"], name="need_type_grp_act_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="orderassessment",
            index=models.Index(fields=["customer_classification_code"], name="ord_assessment_cls_idx"),
        ),
        migrations.AddIndex(
            model_name="orderassessment",
            index=models.Index(fields=["sales_status_code"], name="ord_assessment_sales_idx"),
        ),
        migrations.AddIndex(
            model_name="orderassessment",
            index=models.Index(fields=["delivery_business_status_code"], name="ord_assessment_delivery_idx"),
        ),
        migrations.AddIndex(
            model_name="orderassessment",
            index=models.Index(fields=["created_at"], name="ord_assessment_created_idx"),
        ),
        migrations.AddIndex(
            model_name="orderneed",
            index=models.Index(fields=["order_assessment"], name="order_need_assessment_idx"),
        ),
        migrations.AddIndex(
            model_name="orderneed",
            index=models.Index(fields=["need_type"], name="order_need_type_idx"),
        ),
        migrations.AddIndex(
            model_name="orderjourneyevent",
            index=models.Index(fields=["order", "created_at"], name="ord_journey_order_created_idx"),
        ),
        migrations.AddIndex(
            model_name="orderjourneyevent",
            index=models.Index(fields=["event_type", "created_at"], name="ord_journey_type_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="orderassessment",
            constraint=models.CheckConstraint(
                condition=(models.Q(potential_level__gte=1, potential_level__lte=5) | models.Q(potential_level__isnull=True)),
                name="ord_assessment_potential_1_5",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderneed",
            constraint=models.UniqueConstraint(fields=("order_assessment", "need_type"), name="uniq_order_need_assessment_type"),
        ),
        migrations.RunPython(seed_need_catalog, noop_reverse),
    ]
