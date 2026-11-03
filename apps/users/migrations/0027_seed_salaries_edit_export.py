from django.db import migrations

SALARIES_SECTION = "salaries"
NEW_ACTIONS = ("edit", "export")


def seed(apps, schema_editor):
    """Bổ sung salaries:edit / salaries:export (audit 2026-07-14).

    Trang Cơ chế lương + file bảng lương tháng trước đây gate bằng employees:edit /
    employees:export — role HR có section employees mặc nhiên có cả 2 → xem/sửa được
    lương toàn công ty dù không có salaries:view. KHÔNG gán cho role nào ở đây:
    Administrator (is_system) đã short-circuit full quyền.
    """
    Permission = apps.get_model("users", "Permission")
    for action in NEW_ACTIONS:
        code = f"{SALARIES_SECTION}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code,
                section=SALARIES_SECTION,
                action=action,
                label=f"{SALARIES_SECTION} · {action}",
            )


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của RolePermission.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0026_seed_salaries_permissions")]
    operations = [migrations.RunPython(seed, noop_reverse)]
