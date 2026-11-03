"""Seed quyền `ads-report:edit` — gán chiến dịch quảng cáo về sản phẩm.

CỐ Ý không gán cho role nào (cùng cách 0038 làm với `contacts:export`), và cố ý KHÔNG
grandfather cho các role đang có `ads-report:view` như 0039 đã làm với
`call-quality:edit`: yêu cầu nghiệp vụ là chỉ phòng Ads được sửa. Gán sai một chiến
dịch là đổi toàn bộ chi phí theo sản phẩm trên báo cáo digital, nên admin phải cấp tay
cho đúng role phòng Ads sau khi deploy.

Xem docs/ads-campaign-product-mapping.md §7.
"""

from django.db import migrations

SECTION = "ads-report"
ACTION = "edit"


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    code = f"{SECTION}:{ACTION}"
    if not Permission.objects.filter(code=code).exists():
        Permission.objects.create(
            code=code, section=SECTION, action=ACTION, label=f"{SECTION} · {ACTION}"
        )


def unseed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    Permission.objects.filter(code=f"{SECTION}:{ACTION}").delete()


class Migration(migrations.Migration):
    dependencies = [("users", "0042_team_teammember_team_team_dept_idx_and_more")]
    operations = [migrations.RunPython(seed, unseed)]
