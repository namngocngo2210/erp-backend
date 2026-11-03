"""Seed `pancake-pos:view` + `pancake-pos:edit` (docs/pancake-pos-product-sync.md S2).

Section mới, CỐ Ý không gán role nào: `edit` ghi ra shop Pancake thật. Admin tự
cấp trên UI Vai trò; Administrator (is_system) có sẵn qua short-circuit.
"""

from django.db import migrations

SECTION = "pancake-pos"
ACTIONS = ("view", "edit")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for action in ACTIONS:
        Permission.objects.get_or_create(
            code=f"{SECTION}:{action}",
            defaults={"section": SECTION, "action": action, "label": f"{SECTION} · {action}"},
        )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0052_seed_pancake_leads_edit_permission")]
    operations = [migrations.RunPython(seed, noop_reverse)]
