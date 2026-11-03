from dataclasses import dataclass

from django.db import transaction

from .models import Department, Permission, Role, RoleFieldGrant, RolePermission, UserRoleAssignment
from .rbac import bump_rbac_cache_version
from .rbac_catalog import ORDER_GRANT_SECTION


@dataclass
class UserPermissionResult:
    can_edit: bool
    reason: str


class UserService:
    @staticmethod
    def admin_list_users(q: str | None = None):
        """Queryset danh sách user cho trang quản trị — prefetch đủ quan hệ mà
        UserSerializer cần để tránh N+1 (audit 2026-07 mục 3.5).

        `q`: lọc icontains trên username/display_name (trang Users tìm kiếm
        server-side). order_by("id") để phân trang limit/offset ổn định.
        """
        from django.db.models import Prefetch, Q

        from apps.users.models import User, UserKiotvietMapping

        queryset = (
            User.objects.filter(deleted_at__isnull=True)
            .select_related("department")
            .prefetch_related(
                "role_assignments",
                "department_links",
                # DepartmentSerializer.get_channel_ids đọc qua .all() để ăn cache này.
                # Thiếu dòng này thì mỗi user sinh thêm 1 query đọc channel của phòng
                # ban (đo 2026-07-26: 3 user = 7 query, 15 user = 19). select_related
                # ở trên chỉ kéo được bản ghi Department, không kéo M2M channels.
                "department__channels",
                # Cùng lý do, cho DepartmentSerializer.get_team_count.
                "department__staff_teams",
                Prefetch(
                    "kiotviet_mappings",
                    queryset=UserKiotvietMapping.objects.select_related(
                        "kiotviet_channel"
                    ),
                ),
            )
            .order_by("id")
        )
        if q:
            queryset = queryset.filter(
                Q(username__icontains=q) | Q(display_name__icontains=q)
            )
        return queryset

    @staticmethod
    @transaction.atomic
    def create_admin_user(validated: dict):
        """Tạo User quản trị từ dữ liệu đã validate bởi AdminUserCreateSerializer.

        Dùng chung cho endpoint admin Users và endpoint thêm tài khoản nhân sự.
        """
        from apps.users.models import User, UserKiotvietMapping

        data = dict(validated)
        password = data.pop("password")
        department_id = data.pop("department_id", None)
        kiotviet_mappings = data.pop("kiotviet_mappings", [])
        role_ids = data.pop("role_ids", None)
        department_ids = data.pop("department_ids", None)
        if data.get("media_api_key") is None:
            data["media_api_key"] = ""
        user = User(**data)
        if department_id:
            user.department_id = department_id
        user.set_password(password)
        user.save()

        desired_kv_ids = {e["kiotviet_channel_id"] for e in kiotviet_mappings}
        UserKiotvietMapping.objects.filter(user=user).exclude(
            kiotviet_channel_id__in=desired_kv_ids
        ).delete()
        for entry in kiotviet_mappings:
            UserKiotvietMapping.objects.update_or_create(
                user=user,
                kiotviet_channel_id=entry["kiotviet_channel_id"],
                defaults={"external_kiot_user_id": entry["external_kiot_user_id"]},
            )
        if role_ids is not None:
            RoleService.assign_roles_to_user(user, role_ids)
        if department_ids is not None:
            RoleService.set_user_departments(user, department_ids)
        return user

    @staticmethod
    def can_edit_order(user, order_channel_code: str, assigned_channel_codes: set[str]):
        """Quyền GHI một đơn theo kênh — mirror đúng read-scope.

        - superuser/all scope → toàn quyền
        - channel scope → chỉ kênh được gán trực tiếp (UserChannel)
        - department scope → kênh gán trực tiếp HOẶC kênh thuộc phòng ban của user
        - own scope → không chặn theo kênh tại đây (chặn theo owner ở tầng view)

        Nhánh DEPARTMENT phải hợp thêm `department_channel_codes`, nếu không docstring
        "mirror đúng read-scope" là sai: read-scope DEPARTMENT mở kênh qua UserDepartment
        (apps.users.scoping.resolved_channel_codes), còn `assigned_channel_codes` chỉ đọc
        UserChannel. Hệ quả trước fix: user department-scope THẤY đơn của phòng ban mình
        trên danh sách nhưng KHÔNG sửa/đóng case/xoá được, trừ khi được gán UserChannel
        riêng — chặn oan (audit 2026-07-26).
        """
        from apps.users.models import DataScope
        from apps.users.rbac import resolve_effective_permissions

        eff = resolve_effective_permissions(user)
        if eff.is_superuser:
            return UserPermissionResult(can_edit=True, reason="superuser")

        scope = eff.scope_for("orders")
        if scope in (DataScope.ALL, DataScope.OWN):
            return UserPermissionResult(can_edit=True, reason=f"scope-{scope}")
        if order_channel_code in assigned_channel_codes:
            return UserPermissionResult(can_edit=True, reason="assigned-channel")
        if scope == DataScope.DEPARTMENT:
            from apps.users.scoping import department_channel_codes

            if order_channel_code in department_channel_codes(user):
                return UserPermissionResult(can_edit=True, reason="department-channel")
        return UserPermissionResult(can_edit=False, reason="channel-not-assigned")


class DepartmentService:
    """CRUD phòng ban — business logic tách khỏi view (audit 2026-07 mục 4.4)."""

    DUPLICATE_NAME_MESSAGE = "Ten phong ban da ton tai."

    @staticmethod
    def _sync_channels(dept: Department, channel_ids: list[int]) -> None:
        """Gán đúng danh sách kênh cho phòng ban.

        Quan hệ kênh ↔ phòng ban là nhiều-nhiều, nên ``set()`` chỉ thêm/bớt liên kết
        của RIÊNG phòng ban này — không đụng tới các phòng ban khác đang dùng cùng kênh.
        """
        from apps.channels_core.cache_utils import bump_channels_cache_version
        from apps.channels_core.models import Channel

        channels = Channel.objects.filter(id__in=channel_ids, deleted_at__isnull=True)
        dept.channels.set(channels)
        bump_channels_cache_version()

    @classmethod
    @transaction.atomic
    def create_department(cls, validated: dict) -> Department:
        data = dict(validated)
        channel_ids = data.pop("channel_ids", None)
        if Department.objects.filter(name=data["name"]).exists():
            raise ValueError(cls.DUPLICATE_NAME_MESSAGE)
        dept = Department.objects.create(**data)
        if channel_ids is not None:
            cls._sync_channels(dept, channel_ids)
        return dept

    @classmethod
    @transaction.atomic
    def update_department(cls, dept: Department, validated: dict) -> Department:
        if "name" in validated:
            if Department.objects.filter(name=validated["name"]).exclude(id=dept.id).exists():
                raise ValueError(cls.DUPLICATE_NAME_MESSAGE)
            dept.name = validated["name"]
        if "is_active" in validated:
            dept.is_active = validated["is_active"]
        dept.save()
        if "channel_ids" in validated:
            cls._sync_channels(dept, validated["channel_ids"])
        return dept

    @staticmethod
    def delete_department(dept: Department) -> None:
        dept.delete()


class RoleService:
    DUPLICATE_NAME_MESSAGE = "Tên role đã tồn tại."

    @staticmethod
    def list_roles():
        """Queryset trang Phân quyền — prefetch đủ 4 quan hệ RoleSerializer đọc.

        Để ở service (không dựng thẳng trong view) để test N+1 khoá được đúng thứ
        endpoint dùng: nếu test tự dựng queryset thì xoá prefetch khỏi view vẫn xanh
        (audit 2026-07-26 T1). Thiếu prefetch = 1 query MỖI role MỖI quan hệ.
        """
        return Role.objects.prefetch_related(
            "role_permissions__permission", "section_scopes", "field_rules", "field_grants"
        ).order_by("id")

    @classmethod
    @transaction.atomic
    def create_role(cls, validated: dict) -> Role:
        if Role.objects.filter(name=validated["name"]).exists():
            raise ValueError(cls.DUPLICATE_NAME_MESSAGE)
        role = Role.objects.create(
            name=validated["name"],
            description=validated.get("description", ""),
            scope_default=validated.get("scope_default", "own"),
            is_active=validated.get("is_active", True),
        )
        cls._apply_role_relations(role, validated)
        return role

    @classmethod
    @transaction.atomic
    def update_role(cls, role: Role, validated: dict) -> Role:
        if "name" in validated and Role.objects.filter(name=validated["name"]).exclude(id=role.id).exists():
            raise ValueError(cls.DUPLICATE_NAME_MESSAGE)
        for field_name in ("name", "description", "scope_default", "is_active"):
            if field_name in validated:
                setattr(role, field_name, validated[field_name])
        role.save()
        cls._apply_role_relations(role, validated)
        return role

    @staticmethod
    def delete_role(role: Role) -> None:
        role.delete()
        bump_rbac_cache_version()

    @classmethod
    def _apply_role_relations(cls, role: Role, validated: dict) -> None:
        if "permission_codes" in validated:
            cls.set_role_permissions(role, validated["permission_codes"])
        if "section_scopes" in validated:
            cls.set_role_section_scopes(role, validated["section_scopes"])
        if "field_rules" in validated:
            cls.set_role_field_rules(role, validated["field_rules"])
        if "field_grants" in validated:
            cls.set_role_field_grants(role, validated["field_grants"])

    @staticmethod
    @transaction.atomic
    def set_role_permissions(role: Role, codes: list[str]) -> None:
        perm_ids = list(Permission.objects.filter(code__in=codes).values_list("id", flat=True))
        RolePermission.objects.filter(role=role).delete()
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, permission_id=pid) for pid in perm_ids]
        )
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def set_role_section_scopes(role, items: list[dict]) -> None:
        from .models import RoleSectionScope

        RoleSectionScope.objects.filter(role=role).delete()
        RoleSectionScope.objects.bulk_create(
            [RoleSectionScope(role=role, section=i["section"], scope=i["scope"]) for i in items]
        )
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def set_role_field_rules(role, items: list[dict]) -> None:
        from .models import RoleFieldRule

        RoleFieldRule.objects.filter(role=role).delete()
        RoleFieldRule.objects.bulk_create(
            [
                RoleFieldRule(role=role, section=i["section"], field=i["field"], rule=i["rule"])
                for i in items
            ]
        )
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def set_role_field_grants(role: Role, fields: list[str]) -> None:
        """Thay toàn bộ grant của section orders. Danh sách rỗng = thu hết quyền sửa."""
        RoleFieldGrant.objects.filter(role=role, section=ORDER_GRANT_SECTION).delete()
        RoleFieldGrant.objects.bulk_create(
            [
                RoleFieldGrant(role=role, section=ORDER_GRANT_SECTION, field=f)
                for f in sorted(set(fields))
            ]
        )
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def set_user_departments(user, department_ids: list[int]) -> None:
        from .models import UserDepartment

        target = set(department_ids)
        existing = set(
            UserDepartment.objects.filter(user=user).values_list("department_id", flat=True)
        )
        UserDepartment.objects.filter(user=user, department_id__in=existing - target).delete()
        UserDepartment.objects.bulk_create(
            [UserDepartment(user=user, department_id=did) for did in target - existing]
        )
        # Đảm bảo có 1 primary nếu danh sách không rỗng.
        if target and not UserDepartment.objects.filter(user=user, is_primary=True).exists():
            first = UserDepartment.objects.filter(user=user).order_by("department_id").first()
            if first:
                first.is_primary = True
                first.save(update_fields=["is_primary"])
        # Gỡ kiêm nhiệm một phòng thì phải rụng luôn vai trò trong đội nhóm của phòng đó.
        from .team_services import TeamService

        TeamService.sync_after_department_change(user)
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def assign_roles_to_user(user, role_ids: list[int]) -> None:
        target = set(role_ids)
        existing = set(
            UserRoleAssignment.objects.filter(user=user).values_list("role_id", flat=True)
        )
        UserRoleAssignment.objects.filter(user=user, role_id__in=existing - target).delete()
        UserRoleAssignment.objects.bulk_create(
            [UserRoleAssignment(user=user, role_id=rid) for rid in target - existing]
        )
        bump_rbac_cache_version()
