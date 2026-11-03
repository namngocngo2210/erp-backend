"""Seed + grandfather quyền `customer-phone:view` (xem đầy đủ SĐT khách).

Từ MR này, SĐT khách trên đơn/khách KHÔNG do mình phụ trách bị che (091****678) với
role KHÔNG có quyền này. Để hành vi prod không đổi ngày deploy, mọi Role đang tồn tại
được cấp luôn — siết thật là THU-HỒI-TỪNG-ROLE trên UI (bỏ tick "Xem đầy đủ SĐT khách"
ở role Sale), đúng chiến lược đã dùng cho sweep N2 (migration 0039).

Khác 0039 ở một chỗ: 0039 grandfather theo `{section}:view` tương ứng, còn quyền này
không gắn với section nào — mọi role đều đang thấy số thật hôm nay, nên tập nhận là
TOÀN BỘ role không phải is_system (Administrator đã short-circuit full quyền).
"""

from django.db import migrations

SECTION = "customer-phone"
ACTION = "view"
CODE = f"{SECTION}:{ACTION}"


def seed_and_grandfather(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    Role = apps.get_model("users", "Role")
    RolePermission = apps.get_model("users", "RolePermission")

    # sync_permissions chỉ chạy ở test/manage command, không tự chạy lúc migrate prod.
    permission, _ = Permission.objects.get_or_create(
        code=CODE,
        defaults={"section": SECTION, "action": ACTION, "label": f"{SECTION} · {ACTION}"},
    )

    # Kể cả role đang inactive: grandfather là parity DỮ LIỆU, không phải trạng thái
    # bật/tắt (cùng lập luận với 0039).
    for role_id in Role.objects.filter(is_system=False).values_list("id", flat=True):
        RolePermission.objects.get_or_create(role_id=role_id, permission=permission)

    # Bump thủ công: signal post_save connect với model THẬT, còn RunPython dùng
    # historical model nên signal không bắn — entry cache cũ (TTL 30') sẽ phục vụ tiếp
    # nửa tiếng sau migrate nếu không bump (xem ghi chú ở migration 0039).
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


def noop_reverse(apps, schema_editor):
    """CỐ Ý noop: không phân biệt được RolePermission do grandfather cấp với quyền
    admin cấp tay sau đó — xoá theo code sẽ thu hồi oan. Rollback thật = bỏ tick
    trên UI quản trị. Cũng không xoá Permission (còn FK từ RolePermission).
    """


class Migration(migrations.Migration):
    dependencies = [("users", "0049_seed_pancake_leads_permission")]
    operations = [migrations.RunPython(seed_and_grandfather, noop_reverse)]
