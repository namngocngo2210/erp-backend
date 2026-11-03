from django.db import migrations


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    # Không import sync_permissions trực tiếp (model lịch sử ≠ model runtime). Nhân
    # bản logic catalog tối thiểu tại đây để migration ổn định theo thời gian.
    from apps.users.rbac_catalog import PERMISSION_CATALOG

    desired = {
        f"{section}:{action}": (section, action)
        for section, actions in PERMISSION_CATALOG.items()
        for action in actions
    }
    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(code=code, section=section, action=action, label=f"{section} · {action}")
        for code, (section, action) in desired.items()
        if code not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của Role; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_rbac_models"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, noop_reverse),
    ]
