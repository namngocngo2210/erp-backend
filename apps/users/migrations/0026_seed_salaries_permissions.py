from django.db import migrations

SALARIES_SECTION = "salaries"
SALARIES_ACTIONS = ("view",)


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in SALARIES_ACTIONS:
        code = f"{SALARIES_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=SALARIES_SECTION,
                action=action,
                label=f"{SALARIES_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0025_seed_outside_orders_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
