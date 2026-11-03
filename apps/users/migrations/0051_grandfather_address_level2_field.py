"""Grandfather quyền sửa field `address_level2` (Địa chỉ 2 cấp) — audit 2026-09-23 N1.

Commit 2ce253b0 (17/09) thêm `address_level2` vào OrderInlineUpdateSerializer mà
không khai vào ORDER_FIELD_GROUPS. Hệ field-grant là allow-list fail-closed NHƯNG
chỉ với field CÓ trong catalog (`order_action_view` tính `ungranted` từ
ALL_ORDER_FIELDS) — field ngoài catalog thì ai có PATCH đơn cũng sửa được. Từ MR
này field vào catalog, nên mọi role đang được sửa địa chỉ (có grant `address` hoặc
`ward` của nhóm "Địa chỉ") phải nhận thêm `address_level2`, không thì sale mất
quyền sửa ô "Địa chỉ 2 cấp" ngay giây roll code (mẫu 0037: hệ fail-closed).

Chiều ngược xoá đúng grant `address_level2` (field không tồn tại trước migration
này nên không có grant tay nào để thu hồi oan — khác 0039/0052).
"""

from django.db import migrations

SECTION = "orders"
NEW_FIELD = "address_level2"
# Role có BẤT KỲ field nào trong tập này được coi là "đang sửa được địa chỉ".
SOURCE_FIELDS = ("address", "ward")


def grandfather(apps, schema_editor):
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")

    role_ids = sorted(
        set(
            RoleFieldGrant.objects.filter(section=SECTION, field__in=SOURCE_FIELDS)
            .values_list("role_id", flat=True)
        )
    )
    if not role_ids:
        return

    RoleFieldGrant.objects.bulk_create(
        [RoleFieldGrant(role_id=rid, section=SECTION, field=NEW_FIELD) for rid in role_ids],
        ignore_conflicts=True,
    )


def revoke(apps, schema_editor):
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")
    RoleFieldGrant.objects.filter(section=SECTION, field=NEW_FIELD).delete()


def bump_cache(apps, schema_editor):
    # Signal post_save connect với model THẬT, RunPython dùng historical model nên
    # không bắn — bump tay kẻo cache RBAC (TTL 30') phục vụ tiếp bản cũ (xem 0037).
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


class Migration(migrations.Migration):
    dependencies = [("users", "0050_seed_customer_phone_permission")]

    operations = [
        migrations.RunPython(grandfather, revoke),
        migrations.RunPython(bump_cache, bump_cache),
    ]
