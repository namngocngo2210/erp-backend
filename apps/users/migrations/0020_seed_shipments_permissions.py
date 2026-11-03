from django.db import migrations

SHIPMENTS_SECTION = "shipments"
SHIPMENTS_ACTIONS = ("view", "edit")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in SHIPMENTS_ACTIONS:
        code = f"{SHIPMENTS_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=SHIPMENTS_SECTION,
                action=action,
                label=f"{SHIPMENTS_SECTION} · {action}",
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0019_seed_employees_approve")]
    operations = [migrations.RunPython(seed, noop)]
