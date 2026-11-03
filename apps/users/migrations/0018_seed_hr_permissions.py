from django.db import migrations

HR_SECTION = "employees"
HR_ACTIONS = ("view", "create", "edit", "delete", "export")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(
            code=f"{HR_SECTION}:{a}",
            section=HR_SECTION,
            action=a,
            label=f"{HR_SECTION} · {a}",
        )
        for a in HR_ACTIONS
        if f"{HR_SECTION}:{a}" not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0017_drop_legacy_role_sections")]
    operations = [migrations.RunPython(seed, noop)]
