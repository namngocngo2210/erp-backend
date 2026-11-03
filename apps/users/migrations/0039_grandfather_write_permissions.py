"""Grandfather quyền GHI cho sweep N2 giai đoạn 2 (sale-flow + kho-staff).

Chiến lược đã chốt: các endpoint ghi trước đây chỉ gate `require_section`
(= `{section}:view`) nay gate bằng action thật (create/edit/delete). Để hành vi
prod KHÔNG ĐỔI ngày deploy, mọi Role đang có `{section}:view` được cấp luôn các
action ghi tương ứng — việc siết thật trở thành THU-HỒI-TỪNG-ROLE trên UI sau.

Ghi chú thiết kế:
- KHÔNG đụng role is_system (Administrator): short-circuit full quyền trong
  resolver, cấp thêm là thừa và làm bẩn dữ liệu.
- Idempotent qua get_or_create — chạy lại không nhân đôi.
- `returns:create` CỐ Ý vắng mặt: đợt 1 (a28d9bf6, đã merge & chạy prod) đã siết
  endpoint tạo phiếu trả bằng `returns:create` mà không grandfather — cấp bây
  giờ là NỚI so với hành vi hiện hành, không phải giữ nguyên.
- `call-quality:edit` là action MỚI (thêm vào PERMISSION_CATALOG cùng MR) nên
  Permission row phải được get_or_create tại đây — sync_permissions chỉ chạy
  trong test/manage command, không tự chạy lúc migrate prod.
"""

from django.db import migrations

# (section, action) — mọi role có `{section}:view` nhận `{section}:{action}`.
GRANDFATHER_PAIRS: tuple[tuple[str, str], ...] = (
    ("orders", "create"),
    ("orders", "edit"),
    ("orders", "delete"),
    ("landing-orders", "create"),
    ("landing-orders", "edit"),
    ("landing-orders", "delete"),
    ("koc-orders", "create"),
    ("koc-orders", "edit"),
    ("koc-orders", "delete"),
    ("contacts", "edit"),
    ("stock", "edit"),
    ("stock-documents", "create"),
    ("stock-documents", "edit"),
    ("shipments", "edit"),
    ("floating-pool", "edit"),
    ("call-quality", "edit"),
)


def grandfather(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    RolePermission = apps.get_model("users", "RolePermission")
    Role = apps.get_model("users", "Role")

    # Role thường (kể cả đang inactive — grandfather là parity dữ liệu, không
    # phải trạng thái bật/tắt). Role is_system bị loại tường minh.
    role_ids_with_view: dict[str, list[int]] = {}
    for section, action in GRANDFATHER_PAIRS:
        target, _ = Permission.objects.get_or_create(
            code=f"{section}:{action}",
            defaults={
                "section": section,
                "action": action,
                "label": f"{section} · {action}",
            },
        )
        if section not in role_ids_with_view:
            role_ids_with_view[section] = list(
                Role.objects.filter(
                    is_system=False,
                    role_permissions__permission__code=f"{section}:view",
                )
                .values_list("id", flat=True)
                .distinct()
            )
        for role_id in role_ids_with_view[section]:
            RolePermission.objects.get_or_create(role_id=role_id, permission=target)

    # Bump version cache RBAC THỦ CÔNG: signal post_save của RolePermission trong
    # apps/users/rbac.py được connect với sender là MODEL THẬT
    # (apps.users.models.RolePermission), còn apps.get_model ở trên trả historical
    # model — một class khác — nên post_save của nó KHÔNG khớp sender và signal
    # không bắn. Không bump thì entry cache cũ (TTL 30') vẫn phục vụ tới nửa tiếng
    # sau migrate. Import hàm (không phải model) nên không dính bẫy schema-drift
    # của việc import model thật trong RunPython.
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


def noop_reverse(apps, schema_editor):
    """CỐ Ý noop: không thể phân biệt RolePermission do grandfather cấp với
    quyền admin đã cấp tay trước/sau đó — xoá theo danh sách cặp sẽ thu hồi oan.
    Rollback thật = thu hồi từng role trên UI quản trị (đúng chiến lược đã chốt).
    """


class Migration(migrations.Migration):
    dependencies = [("users", "0038_seed_contacts_export_permission")]
    operations = [migrations.RunPython(grandfather, noop_reverse)]
