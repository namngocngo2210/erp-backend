"""Seed quyền `call-quality:export` (xuất Excel trang Chất lượng cuộc gọi).

CỐ Ý không gán cho role nào (cùng cách 0038 làm với `contacts:export`): file xuất
gom SĐT khách và trích dẫn transcript — trong đó có thông tin bệnh khách kể —
của cả kỳ vào một tệp mang đi được. Xem trên màn hình đã gate bằng
`call-quality:view`, nhưng mang cả tập ra ngoài là mức rủi ro khác, phải do admin
cấp tay cho đúng role.

Sau khi deploy, không ai (ngoài Administrator — role is_system, short-circuit full
quyền) thấy nút Xuất Excel cho tới khi được cấp.
"""

from django.db import migrations

SECTION = "call-quality"
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
    dependencies = [("users", "0043_seed_ads_report_edit_permission")]
    operations = [migrations.RunPython(seed, noop)]
