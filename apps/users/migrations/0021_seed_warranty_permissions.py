from django.db import migrations

WARRANTY_SECTION = "warranty"
WARRANTY_ACTIONS = ("view", "create", "edit", "delete", "export")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in WARRANTY_ACTIONS:
        code = f"{WARRANTY_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=WARRANTY_SECTION,
                action=action,
                label=f"{WARRANTY_SECTION} · {action}",
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0020_seed_shipments_permissions")]
    operations = [migrations.RunPython(seed, noop)]
