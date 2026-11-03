"""Cấp đủ field cho role đang sửa được đơn — giữ nguyên hành vi ngày deploy.

Fail-closed mà không seed = cả công ty mất quyền sửa đơn ngay giây roll code.
Cấp bằng migration thay vì cấp tay (như đợt sale-assignment 2026-08-07) vì ở đây là
~50 field × mỗi role: cấp tay là cầm chắc sai sót và downtime.

VÌ SAO SEED THEO `:view` CHỨ KHÔNG PHẢI `:edit`: endpoint sửa đơn
(`update_order_inline`) gác bằng `require_section(*ORDER_SECTIONS, "shipments")`, mà
`require_section` = `{section}:view` (apps/users/permissions.py). KHÔNG có chỗ nào
check `:edit`. Nghĩa là hôm nay bất kỳ role nào có `orders:view` (trong record-scope
+ can_edit_channel) đều sửa được đơn — seed theo `:edit` sẽ cắt quyền của các role
view-only đang sửa đơn hằng ngày, đúng thứ spec cấm.

Seed dư thì admin bỏ tick ở bước siết quyền; seed thiếu thì cả phòng mất quyền không
báo trước. Chọn chiều fail-safe (không xác minh được trên prod: DB đang timeout).

Role tạo MỚI sau migration này: 0 grant → fail-closed đúng thiết kế.
"""

from django.db import migrations

# Mọi mã quyền MỞ ĐƯỢC đường PATCH /api/orders/{id}. `:view` là mã thật sự gác
# endpoint; `:edit` giữ trong danh sách cho role chỉ có edit mà thiếu view.
ORDER_ACCESS_CODES = (
    "orders:view",
    "orders:edit",
    "landing-orders:view",
    "landing-orders:edit",
    "koc-orders:view",
    "koc-orders:edit",
    "shipments:view",
    "shipments:edit",
)


def seed(apps, schema_editor):
    from apps.users.rbac_catalog import ALL_ORDER_FIELDS

    Role = apps.get_model("users", "Role")
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")

    role_ids = set(
        Role.objects.filter(
            role_permissions__permission__code__in=ORDER_ACCESS_CODES
        ).values_list("id", flat=True)
    )
    RoleFieldGrant.objects.bulk_create(
        [
            RoleFieldGrant(role_id=rid, section="orders", field=f)
            for rid in sorted(role_ids)
            for f in sorted(ALL_ORDER_FIELDS)
        ],
        ignore_conflicts=True,
    )


def unseed(apps, schema_editor):
    # Xoá MỌI grant section "orders", kể cả grant admin tự tạo sau deploy — xem mục
    # M2 trong docs/order-field-permission-runbook.md trước khi rollback.
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")
    RoleFieldGrant.objects.filter(section="orders").delete()


def bump_cache(apps, schema_editor):
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


class Migration(migrations.Migration):
    dependencies = [("users", "0035_role_field_grant")]

    operations = [
        migrations.RunPython(seed, unseed),
        migrations.RunPython(bump_cache, migrations.RunPython.noop),
    ]
