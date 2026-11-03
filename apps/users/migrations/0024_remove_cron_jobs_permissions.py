from django.db import migrations


def remove_cron_jobs_rbac(apps, schema_editor):
    """Trang Cron Jobs bị gỡ (2026-07-06) — dọn quyền/scope/field-rule của section.

    Permission cascade xoá RolePermission; RoleSectionScope/RoleFieldRule khoá
    theo chuỗi section nên xoá tường minh.
    """
    Permission = apps.get_model("users", "Permission")
    RoleSectionScope = apps.get_model("users", "RoleSectionScope")
    RoleFieldRule = apps.get_model("users", "RoleFieldRule")
    Permission.objects.filter(section="cron-jobs").delete()
    RoleSectionScope.objects.filter(section="cron-jobs").delete()
    RoleFieldRule.objects.filter(section="cron-jobs").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0023_grant_returns_approve_parity"),
    ]

    operations = [
        migrations.RunPython(remove_cron_jobs_rbac, migrations.RunPython.noop),
    ]
