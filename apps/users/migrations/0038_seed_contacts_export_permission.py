"""Seed quyền `contacts:export` (xuất Excel trang Danh sách contact).

CỐ Ý không gán cho role nào: file xuất chứa toàn bộ danh bạ + SĐT khách trong scope
người xuất, nên admin phải cấp tay cho đúng role. Sau khi deploy, không ai (ngoài
Administrator — role is_system, short-circuit full quyền) thấy nút Xuất Excel cho tới
khi được cấp.
"""

from django.db import migrations

SECTION = "contacts"
ACTION = "export"


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    code = f"{SECTION}:{ACTION}"
    if not Permission.objects.filter(code=code).exists():
        Permission.objects.create(
            code=code, section=SECTION, action=ACTION, label=f"{SECTION} · {ACTION}"
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0037_expand_crm_field_grants")]
    operations = [migrations.RunPython(seed, noop)]
