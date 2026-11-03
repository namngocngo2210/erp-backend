from django.db import migrations

FLOATING_POOL_SECTION = "floating-pool"
FLOATING_POOL_ACTIONS = ("view", "edit")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in FLOATING_POOL_ACTIONS:
        code = f"{FLOATING_POOL_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=FLOATING_POOL_SECTION,
                action=action,
                label=f"{FLOATING_POOL_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0028_drop_sync_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
