"""Seed 5 bước tác nghiệp chăm sóc sau bán: HDSD, D3, D7, D15, D30.

Idempotent theo `name` (khoá upsert của luồng import Excel): bước đã tồn tại thì bỏ
qua hoàn toàn, KHÔNG ghi đè màu/cờ của bản admin đã chỉnh tay.

Cố ý KHÔNG sinh `OperationTransition` nào: 5 bước này dùng để sale chọn tay ở dropdown
cột Tác nghiệp. Muốn chuỗi HDSD → D3 → D7 → D15 → D30 tự nhảy sau khi ghi kết quả thì
phải cấu hình rule ở trang Cấu hình tác nghiệp (hoặc một migration riêng khi đã chốt
dùng Kết quả nào làm điều kiện).

`allow_edit_due=True` cho cả 5: đây là lịch chăm sóc theo ngày, sale phải dời được hẹn.
"""

from django.db import migrations

# (name, color) — màu chỉ để phân biệt badge trên bảng contact.
STAGES = [
    ("HDSD", "#0ea5e9"),
    ("D3", "#22c55e"),
    ("D7", "#14b8a6"),
    ("D15", "#f59e0b"),
    ("D30", "#8b5cf6"),
]


def seed(apps, schema_editor):
    OperationStage = apps.get_model("contact_flow", "OperationStage")
    existing = set(
        OperationStage.objects.filter(name__in=[name for name, _ in STAGES]).values_list(
            "name", flat=True
        )
    )
    # Nối tiếp sau bước cuối cùng đang có để không chen ngang thứ tự admin đã sắp.
    positions = OperationStage.objects.values_list("position", flat=True)
    next_position = (max(positions) + 1) if positions else 0

    for offset, (name, color) in enumerate(
        [(name, color) for name, color in STAGES if name not in existing]
    ):
        OperationStage.objects.create(
            name=name,
            color=color,
            is_start=False,
            allow_edit_due=True,
            is_active=True,
            position=next_position + offset,
        )


def unseed(apps, schema_editor):
    """Chỉ xoá bước CHƯA được dùng: `CustomerOperationState.current_stage` là PROTECT,
    xoá mù sẽ nổ ProtectedError giữa lúc rollback."""
    OperationStage = apps.get_model("contact_flow", "OperationStage")
    OperationStage.objects.filter(
        name__in=[name for name, _ in STAGES],
        customeroperationstate__isnull=True,
        transitions_out__isnull=True,
        transitions_in__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("contact_flow", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
