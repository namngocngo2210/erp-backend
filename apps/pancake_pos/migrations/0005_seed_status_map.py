"""Seed ghép trạng thái đơn Pancake ↔ `OrderStatus` CRM (two-way-sync §3, A2).

Enum trạng thái đơn Pancake lấy từ spec Open API (POST/GET orders `status`). Chỉ
thêm dòng CHƯA có (người vận hành có thể đã sửa) — chạy lại vô hại. `locks_edit` =
kho Pancake đã xử lý (in phiếu, đóng, xuất, giao) → CRM không PUT/huỷ đơn đó nữa,
chỉ cảnh báo (§4.1).

CRM `OrderStatus`: 1 chờ xử lý, 2 đang giao, 3 giao thành công, 4 đang hoàn,
5 đã hoàn, 6 huỷ, 7 đang lấy hàng, 11 chờ giao.
"""

from django.db import migrations

# (pancake_status, tên, crm_status, locks_edit)
DEFAULT_STATUS_MAP = (
    (0, "Mới", 1, False),
    (17, "Chờ xác nhận", 1, False),
    (11, "Chờ hàng", 1, False),
    (12, "Chờ in", 1, False),
    (13, "Đã in", 11, True),
    (20, "Đã đặt hàng", 1, False),
    (1, "Đã xác nhận", 1, False),
    (8, "Đang đóng hàng", 11, True),
    (9, "Chờ chuyển hàng", 7, True),
    (2, "Đã gửi hàng", 2, True),
    (3, "Đã nhận", 3, True),
    (16, "Đã thu tiền", 3, True),
    (4, "Đang hoàn", 4, True),
    (15, "Hoàn một phần", 3, True),
    (5, "Đã hoàn", 5, True),
    (6, "Đã huỷ", 6, False),
    (7, "Đã xoá", 6, False),
)


def seed(apps, schema_editor):
    StatusMap = apps.get_model("pancake_pos", "PancakePosStatusMap")
    existing = set(StatusMap.objects.values_list("pancake_status", flat=True))
    StatusMap.objects.bulk_create([
        StatusMap(pancake_status=code, pancake_name=name, crm_status=crm, locks_edit=locks)
        for code, name, crm, locks in DEFAULT_STATUS_MAP
        if code not in existing
    ])


class Migration(migrations.Migration):

    dependencies = [("pancake_pos", "0004_order_links_status_map")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
