from django.db import migrations

# Section mới + action edit bổ sung cho loyal-customers. Seed thẳng thay vì gọi
# sync_permissions() để migration không phụ thuộc code catalog ở thời điểm chạy.
NEW_PERMISSIONS = (
    ("sale-assignment", "edit"),
    ("loyal-customers", "edit"),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for section, action in NEW_PERMISSIONS:
        code = f"{section}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code, section=section, action=action, label=f"{section} · {action}"
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0032_seed_warranty_codes_permissions")]
    operations = [migrations.RunPython(seed, noop)]
