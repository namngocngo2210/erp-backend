from django.db import migrations

OUTSIDE_ORDERS_SECTION = "outside-orders"
OUTSIDE_ORDERS_ACTIONS = ("view", "create", "edit", "delete", "export")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in OUTSIDE_ORDERS_ACTIONS:
        code = f"{OUTSIDE_ORDERS_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=OUTSIDE_ORDERS_SECTION,
                action=action,
                label=f"{OUTSIDE_ORDERS_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0024_remove_cron_jobs_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
