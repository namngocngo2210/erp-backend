# Verbatim copy of apps/orders/migrations/0052_add_buy_for_self_need_group.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations


def add_buy_for_self_need_group(apps, schema_editor):
    NeedGroup = apps.get_model("orders", "NeedGroup")
    NeedType = apps.get_model("orders", "NeedType")

    group, _ = NeedGroup.objects.update_or_create(
        code="buy_for_self",
        defaults={
            "name_vi": "Mua cho bản thân",
            "sort_order": 35,
            "is_active": True,
        },
    )
    NeedType.objects.update_or_create(
        code="self_use",
        defaults={
            "group": group,
            "name_vi": "Mua cho bản thân dùng",
            "sort_order": 10,
            "is_active": True,
        },
    )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0051_order_is_delivery_invoice_printed"),
    ]

    operations = [
        migrations.RunPython(add_buy_for_self_need_group, noop_reverse),
    ]
