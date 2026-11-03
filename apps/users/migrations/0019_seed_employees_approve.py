from django.db import migrations

HR_SECTION = "employees"
HR_ACTION = "approve"


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    code = f"{HR_SECTION}:{HR_ACTION}"
    if not Permission.objects.filter(code=code).exists():
        Permission.objects.create(
            code=code,
            section=HR_SECTION,
            action=HR_ACTION,
            label=f"{HR_SECTION} · {HR_ACTION}",
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0018_seed_hr_permissions")]
    operations = [migrations.RunPython(seed, noop)]
