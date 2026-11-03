"""Gỡ section `outside-orders` khỏi catalog quyền RBAC.

Trang Đơn gửi ngoài đã bị xóa (2026-08-11). Xóa `Permission` của section này
cascade luôn `rbac_role_permissions`, nên role nào đang được cấp cũng mất quyền
trỏ vào hư không. `RoleSectionScope` phải xóa riêng vì không có FK tới Permission.

reverse để no-op: seed lại quyền cho một trang không còn tồn tại là vô nghĩa,
và migration ngược chỉ cần chạy được để không chặn `migrate <app> <number>`.
"""
from django.db import migrations

SECTION = "outside-orders"


def remove_section(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    RoleSectionScope = apps.get_model("users", "RoleSectionScope")
    Permission.objects.filter(section=SECTION).delete()
    RoleSectionScope.objects.filter(section=SECTION).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0033_seed_sale_assignment_permissions"),
    ]

    operations = [
        migrations.RunPython(remove_section, noop),
    ]
