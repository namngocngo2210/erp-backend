from django.db import migrations

OVERVIEW_SECTION = "overview-report"
OVERVIEW_ACTIONS = ("view",)


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in OVERVIEW_ACTIONS:
        code = f"{OVERVIEW_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=OVERVIEW_SECTION,
                action=action,
                label=f"{OVERVIEW_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0030_sync_contact_flow_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
