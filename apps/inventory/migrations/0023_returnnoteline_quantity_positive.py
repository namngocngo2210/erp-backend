"""quantity=0 dòng phiếu trả vô nghĩa (audit followup 2026-07 mục 4.3).

Chỉ đường auto-fill (return_entry_edit_service.update_draft, khi tổng qty item
của đơn cho 1 product = 0) từng có thể lọt qua; đường nhập tay đã chặn ở
serializer (min_value=1). Data-fix xoá CHỈ dòng qty=0 thuộc entry còn ở trạng
thái draft (an toàn — nháp chưa ghi sổ/ảnh hưởng tồn); nếu còn sót dòng qty=0
ở entry posted/cancelled (không nên xảy ra), AddConstraint bên dưới sẽ chặn
đứng migration thay vì âm thầm xoá dữ liệu lịch sử.
"""
from django.db import migrations, models
from django.db.models import Q


def delete_zero_qty_draft_lines(apps, schema_editor):
    ReturnNoteLine = apps.get_model("inventory", "ReturnNoteLine")
    ReturnNoteLine.objects.filter(quantity=0, entry__status="draft").delete()


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0022_return_entry'),
    ]

    operations = [
        migrations.RunPython(delete_zero_qty_draft_lines, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='returnnoteline',
            constraint=models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name='returnline_quantity_positive',
            ),
        ),
    ]
