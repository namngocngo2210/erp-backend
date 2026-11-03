from django.db import migrations

CONTACT_FLOW_SECTION = "contact_flow"
CONTACT_FLOW_ACTIONS = ("view", "edit")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in CONTACT_FLOW_ACTIONS:
        code = f"{CONTACT_FLOW_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=CONTACT_FLOW_SECTION,
                action=action,
                label=f"{CONTACT_FLOW_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0029_seed_floating_pool_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
