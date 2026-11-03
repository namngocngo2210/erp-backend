from django.db import migrations

SECTION = "warranty-codes"
ACTIONS = ("view", "create", "edit", "export")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in ACTIONS:
        code = f"{SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(code=code, section=SECTION, action=action, label=f"{SECTION} · {action}")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0031_seed_overview_report_permissions")]
    operations = [migrations.RunPython(seed, noop)]
