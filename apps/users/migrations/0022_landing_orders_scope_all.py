"""Fix regression: trang Landing chỉ hiện đơn đã 'phân tự động' cho từng user.

Sau commit 221f9c03, list Landing resolve data-scope theo section 'landing-orders'
(trước đó hardcode 'orders'). Role không có RoleSectionScope override cho
'landing-orders' rơi về Role.scope_default (mặc định OWN) → _apply_user_channel_scope
thêm filter staff_id=user.id → mỗi user chỉ thấy đơn đã phân cho chính mình, đơn
chưa phân (staff=NULL) hoặc phân cho người khác biến mất.

Nghiệp vụ: trang Landing là pool chung — MỌI user (kể cả sale) phải thấy tất cả đơn.
=> Set scope 'landing-orders' = ALL cho mọi role đang cấp quyền landing-orders:view
(role is_system đã full-quyền qua short-circuit superuser nên bỏ qua).
"""

from django.db import migrations

LANDING_SECTION = "landing-orders"
ALL_SCOPE = "all"


def set_landing_scope_all(apps, schema_editor):
    Role = apps.get_model("users", "Role")
    RolePermission = apps.get_model("users", "RolePermission")
    RoleSectionScope = apps.get_model("users", "RoleSectionScope")

    role_ids = set(
        RolePermission.objects.filter(
            permission__section=LANDING_SECTION
        ).values_list("role_id", flat=True)
    )
    for role in Role.objects.filter(id__in=role_ids, is_system=False):
        RoleSectionScope.objects.update_or_create(
            role=role,
            section=LANDING_SECTION,
            defaults={"scope": ALL_SCOPE},
        )

    # Historical models không kích hoạt signal bust cache RBAC → bump thủ công để
    # EffectivePermissions đang cache (TTL 30') bị vô hiệu ngay sau khi migrate.
    try:
        from apps.users.rbac import bump_rbac_cache_version

        bump_rbac_cache_version()
    except Exception:
        pass


def noop(apps, schema_editor):
    # Không thể khôi phục scope cũ (mỗi role có thể vốn khác nhau) một cách an toàn.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0021_seed_warranty_permissions")]
    operations = [migrations.RunPython(set_landing_scope_all, noop)]
