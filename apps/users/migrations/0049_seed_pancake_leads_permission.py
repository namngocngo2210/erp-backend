from django.db import migrations

SECTION = "pancake-leads"
ACTIONS = ("view",)


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in ACTIONS:
        code = f"{SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code, section=SECTION, action=action, label=f"{SECTION} · {action}"
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0048_user_foxia_id")]
    operations = [migrations.RunPython(seed, noop_reverse)]
