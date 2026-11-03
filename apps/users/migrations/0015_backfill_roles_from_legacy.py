from django.db import migrations

# Hằng số nội tại migration — KHÔNG import từ runtime models (enum/field legacy đã bị
# gỡ ở 0017). Backfill chạy trên historical models nên dùng giá trị chuỗi cố định.
DEPTLESS_STAFF_SECTIONS = ("orders", "landing-orders", "koc-orders", "customers", "resale-tracking")
DEPTLESS_ROLE_NAME = "Nhân viên mặc định"
ADMIN_ROLE_NAME = "Administrator"
ADS_DEPARTMENT_ID = 2  # mirror apps.users.constants.DepartmentID.ADS
SCOPE_ALL = "all"
SCOPE_CHANNEL = "channel"
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"


def _grant_sections(Permission, RolePermission, role, sections) -> None:
    perms = Permission.objects.filter(section__in=list(sections))
    existing = set(
        RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
    )
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, permission=p) for p in perms if p.id not in existing]
    )


def run_backfill(apps, schema_editor):
    """Ánh xạ trạng thái phân quyền cũ (User.role + Department.allowed_sections) sang
    Role/RolePermission/RoleSectionScope/UserRoleAssignment. Idempotent.

    Dùng historical models nên đọc được field legacy ``role``/``allowed_sections``
    còn tồn tại ở trạng thái lịch sử này (chúng chỉ bị gỡ ở 0017).
    """
    User = apps.get_model("users", "User")
    Department = apps.get_model("users", "Department")
    Permission = apps.get_model("users", "Permission")
    Role = apps.get_model("users", "Role")
    RolePermission = apps.get_model("users", "RolePermission")
    RoleSectionScope = apps.get_model("users", "RoleSectionScope")
    UserRoleAssignment = apps.get_model("users", "UserRoleAssignment")

    def _assign(user, role):
        UserRoleAssignment.objects.get_or_create(user=user, role=role)

    # 1) Administrator
    admin_role, _ = Role.objects.get_or_create(
        name=ADMIN_ROLE_NAME,
        defaults={"is_system": True, "scope_default": SCOPE_ALL, "description": "Toàn quyền hệ thống"},
    )
    for admin in User.objects.filter(role=ROLE_ADMIN, deleted_at__isnull=True):
        _assign(admin, admin_role)

    # 2) Role theo từng phòng ban
    dept_role_by_id: dict[int, object] = {}
    for dept in Department.objects.all():
        role, _ = Role.objects.get_or_create(
            name=f"Phòng: {dept.name}",
            defaults={"scope_default": SCOPE_ALL, "description": f"Auto từ phòng ban {dept.name}"},
        )
        _grant_sections(Permission, RolePermission, role, dept.allowed_sections or [])
        if dept.id == ADS_DEPARTMENT_ID:
            RoleSectionScope.objects.get_or_create(
                role=role, section="orders", defaults={"scope": SCOPE_CHANNEL}
            )
        dept_role_by_id[dept.id] = role

    for staff in User.objects.filter(
        role=ROLE_STAFF, department__isnull=False, deleted_at__isnull=True
    ):
        role = dept_role_by_id.get(staff.department_id)
        if role is not None:
            _assign(staff, role)

    # 3) Role mặc định cho STAFF không phòng ban
    deptless_role, _ = Role.objects.get_or_create(
        name=DEPTLESS_ROLE_NAME,
        defaults={"scope_default": SCOPE_ALL, "description": "STAFF chưa gán phòng ban"},
    )
    _grant_sections(Permission, RolePermission, deptless_role, DEPTLESS_STAFF_SECTIONS)
    for staff in User.objects.filter(
        role=ROLE_STAFF, department__isnull=True, deleted_at__isnull=True
    ):
        _assign(staff, deptless_role)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0014_seed_rbac_catalog"),
    ]

    operations = [
        migrations.RunPython(run_backfill, noop_reverse),
    ]
