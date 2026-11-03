"""Seed + grandfather `pancake-leads:edit` — audit 2026-09-23 M1.

`POST /api/pancake/leads/<id>/retry-order` xếp lịch TẠO ĐƠN THẬT từ lead nhưng
gate bằng `pancake-leads:view` (catalog chỉ có "view"). Từ MR này endpoint gate
bằng `pancake-leads:edit`. Để hành vi prod không đổi ngày deploy, mọi role đang
có `:view` nhận luôn `:edit` — siết thật = thu hồi từng role trên UI (chiến
lược 0039). Role is_system (Administrator) không đụng: resolver short-circuit.
"""

from django.db import migrations

SECTION = "pancake-leads"
VIEW_CODE = f"{SECTION}:view"
EDIT_CODE = f"{SECTION}:edit"


def seed_and_grandfather(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    Role = apps.get_model("users", "Role")
    RolePermission = apps.get_model("users", "RolePermission")

    # sync_permissions chỉ chạy ở test/manage command, không tự chạy lúc migrate prod.
    permission, _ = Permission.objects.get_or_create(
        code=EDIT_CODE,
        defaults={"section": SECTION, "action": "edit", "label": f"{SECTION} · edit"},
    )

    # Kể cả role inactive: grandfather là parity DỮ LIỆU (cùng lập luận 0039).
    role_ids = (
        Role.objects.filter(is_system=False, role_permissions__permission__code=VIEW_CODE)
        .values_list("id", flat=True)
        .distinct()
    )
    for role_id in role_ids:
        RolePermission.objects.get_or_create(role_id=role_id, permission=permission)

    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


def noop_reverse(apps, schema_editor):
    """CỐ Ý noop: không phân biệt được RolePermission do grandfather cấp với quyền
    admin cấp tay sau đó — xoá theo code sẽ thu hồi oan (xem 0039/0050)."""


class Migration(migrations.Migration):
    dependencies = [("users", "0051_grandfather_address_level2_field")]
    operations = [migrations.RunPython(seed_and_grandfather, noop_reverse)]
