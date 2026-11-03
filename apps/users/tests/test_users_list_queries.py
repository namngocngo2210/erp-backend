"""Chặn N+1 cho danh sách user admin (audit hardening 2026-07 mục 3.5).

UserSerializer trước đây query mới cho mỗi user (role_assignments, department_links,
kiotviet_mappings) → 10 user = 30+ query. Sau fix: queryset prefetch + serializer đọc
prefetch cache.

Audit 2026-09-23 T1 §2.2: phiên bản cũ của file này PATCH `resolve_effective_permissions`
nên không bắt được N+1 RBAC (cache lạnh = 5 SQL/user, đo 54→104 query khi 10→20
user). Giờ đo THẬT với cache lạnh (bump version ngay trước phép đo): danh sách đi qua
`resolve_effective_permissions_bulk` nên số query là hằng theo số user.
"""
from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.kiotviet.models import KiotVietChannel
from apps.users.models import (
    Department,
    Role,
    User,
    UserDepartment,
    UserKiotvietMapping,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version, resolve_effective_permissions
from apps.users.serializers import UserSerializer
from apps.users.services import UserService


def _measure_cold(queryset) -> tuple[int, list]:
    """Số query serialize danh sách với cache RBAC LẠNH.

    Bump version ngay trước phép đo thay vì patch resolver: cache RBAC nằm ở Redis và
    có thể bị bump bởi test run khác dùng chung Redis DB — bump giữa chừng chỉ làm
    cache lạnh thêm, mà đường bulk đã là hằng số khi lạnh nên phép đo vẫn tiền định.
    """
    bump_rbac_cache_version()
    with CaptureQueriesContext(connection) as ctx:
        data = UserSerializer(queryset, many=True).data
    return len(ctx.captured_queries), data


class AdminUsersListQueryCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="Phòng QC")
        role = Role.objects.create(name="qc-role-nplus1", scope_default="own")
        kv = KiotVietChannel.objects.create(name="KV test", retailer="testshop")
        for i in range(10):
            user = User.objects.create_user(
                username=f"nplus1_user_{i}", password="x-not-used-1"
            )
            UserRoleAssignment.objects.create(user=user, role=role)
            UserDepartment.objects.create(user=user, department=dept, is_primary=True)
            UserKiotvietMapping.objects.create(
                user=user, kiotviet_channel=kv, external_kiot_user_id=f"kv{i}"
            )

    def test_users_list_query_ceiling_cold_rbac(self):
        # Trần cứng khi cache RBAC LẠNH: 1 users + 4 prefetch + (1 gán role + 4 bảng
        # rule theo role_id__in) = 10; để dư 2 cho prefetch hợp lệ thêm sau này.
        with CaptureQueriesContext(connection) as ctx:
            bump_rbac_cache_version()
            data = UserSerializer(UserService.admin_list_users(), many=True).data

        self.assertGreaterEqual(len(data), 10)
        self.assertLessEqual(
            len(ctx.captured_queries), 12,
            f"users list phải ≤12 query (cache RBAC lạnh) cho 10 users, thấy "
            f"{len(ctx.captured_queries)}:\n"
            + "\n".join(q["sql"][:120] for q in ctx.captured_queries),
        )

    def test_rbac_queries_do_not_grow_with_user_count_cold_cache(self):
        """N+1 RBAC (audit 2026-09-23 T1 §2.2): 10 user và 20 user phải cùng số query."""
        small, small_data = _measure_cold(UserService.admin_list_users())

        dept = Department.objects.get(name="Phòng QC")
        role = Role.objects.get(name="qc-role-nplus1")
        kv = KiotVietChannel.objects.get(retailer="testshop")
        for i in range(10, 20):
            user = User.objects.create_user(username=f"nplus1_user_{i}", password="x-not-used-1")
            UserRoleAssignment.objects.create(user=user, role=role)
            UserDepartment.objects.create(user=user, department=dept, is_primary=True)
            UserKiotvietMapping.objects.create(
                user=user, kiotviet_channel=kv, external_kiot_user_id=f"kv{i}"
            )
        large, large_data = _measure_cold(UserService.admin_list_users())

        self.assertGreaterEqual(len(small_data), 10)
        self.assertGreaterEqual(len(large_data), 20)
        self.assertEqual(
            large, small,
            f"N+1 RBAC: 10 user = {small} query, 20 user = {large} query "
            "(cache lạnh). Chênh lệch = resolve quyền lẻ theo từng user.",
        )

    def test_bulk_matches_single_resolve(self):
        """Đường bulk (danh sách) và đường lẻ (instance rời) phải cho cùng quyền."""
        users = list(UserService.admin_list_users())
        listed = {row["id"]: row for row in UserSerializer(users, many=True).data}
        for user in users:
            single = UserSerializer(user).data
            self.assertEqual(listed[user.id]["allowed_sections"], single["allowed_sections"])
            self.assertEqual(
                listed[user.id]["editable_order_fields"], single["editable_order_fields"]
            )
            eff = resolve_effective_permissions(user)
            self.assertFalse(eff.is_superuser)
            self.assertEqual(eff.scope_for("orders"), "own")

    def test_serializer_output_shape_unchanged(self):
        user = User.objects.get(username="nplus1_user_0")
        data = UserSerializer(
            UserService.admin_list_users().filter(id=user.id), many=True
        ).data[0]
        self.assertEqual(data["role_ids"], [Role.objects.get(name="qc-role-nplus1").id])
        self.assertEqual(len(data["department_ids"]), 1)
        self.assertEqual(data["kiotviet_mappings"][0]["external_kiot_user_id"], "kv0")

    def test_serializer_works_without_prefetch(self):
        # Serializer phải fallback an toàn khi được dùng ở endpoint khác
        # không prefetch (chậm hơn nhưng đúng).
        user = User.objects.get(username="nplus1_user_1")
        data = UserSerializer(user).data
        self.assertEqual(len(data["role_ids"]), 1)
        self.assertEqual(len(data["department_ids"]), 1)
        self.assertEqual(data["kiotviet_mappings"][0]["external_kiot_user_id"], "kv1")


def _make_users(count: int, department: Department, role: Role, prefix: str) -> None:
    for i in range(count):
        user = User.objects.create_user(
            username=f"{prefix}_{i}", password="x-not-used-1", department=department
        )
        UserRoleAssignment.objects.create(user=user, role=role)


class DepartmentChannelsNPlusOneTests(TestCase):
    """N+1 qua DepartmentSerializer trên danh sách user admin (audit 2026-07-26 T1).

    Vì sao cần lớp riêng dù đã có AdminUsersListQueryCountTests: fixture ở đó tạo user
    bằng `create_user()` nên `User.department` (FK legacy) là NULL — mà
    `UserSerializer.department` đọc đúng FK đó. Nested DepartmentSerializer vì thế CHƯA
    BAO GIỜ chạy trong phép đo cũ, nên N+1 của `get_channel_ids` không lộ ra dù trần
    ≤6 query vẫn xanh.

    Phép đo ở đây so SỐ QUERY GIỮA HAI CỠ TRANG thay vì chốt trần cứng: trần cứng phải
    sửa mỗi lần thêm một prefetch hợp lệ, còn "3 user và 15 user phải bằng nhau" thì chỉ
    đỏ đúng khi có query mọc theo số dòng — tức đúng định nghĩa N+1.
    """

    @classmethod
    def setUpTestData(cls):
        # get_model thay vì import trực tiếp: giữ apps.users khỏi phụ thuộc tĩnh vào
        # apps.channels_core trong test (cùng cách các test quyền đơn hàng đang dùng).
        channel_model = django_apps.get_model("channels_core", "Channel")

        cls.department = Department.objects.create(name="Phòng Kinh doanh N+1")
        cls.role = Role.objects.create(name="dept-chan-nplus1", scope_default="own")
        for idx in range(2):
            channel = channel_model.objects.create(
                name=f"Kênh N+1 {idx}", code=f"nplus1-ch-{idx}"
            )
            channel.departments.add(cls.department)

        _make_users(3, cls.department, cls.role, "deptch_small")

    def _measure(self) -> int:
        # Đo THẬT với cache RBAC lạnh (không patch resolver nữa — audit 2026-09-23
        # T1 §2.2): đường bulk là hằng số nên phép đo vẫn tiền định dù Redis bị
        # bump giữa chừng bởi test run khác.
        count, data = _measure_cold(UserService.admin_list_users())
        self.assertTrue(all(row["department"] is not None for row in data))
        return count

    def test_query_count_does_not_grow_with_user_count(self):
        small = self._measure()

        _make_users(12, self.department, self.role, "deptch_large")
        large = self._measure()

        self.assertEqual(
            large,
            small,
            f"N+1: 3 user = {small} query, 15 user = {large} query. "
            "Chênh lệch nghĩa là có query mọc theo từng dòng — thường do serializer "
            "gọi .filter() trên related manager (bỏ qua prefetch cache) thay vì .all().",
        )

    def test_channel_ids_still_exclude_soft_deleted_channels(self):
        """Prefetch không được làm mất bộ lọc deleted_at của get_channel_ids."""
        from django.utils import timezone

        channel_model = django_apps.get_model("channels_core", "Channel")
        deleted = channel_model.objects.create(
            name="Kênh đã xoá", code="nplus1-ch-del", deleted_at=timezone.now()
        )
        deleted.departments.add(self.department)

        row = UserSerializer(
            UserService.admin_list_users().filter(username="deptch_small_0"), many=True
        ).data[0]

        self.assertNotIn(deleted.id, row["department"]["channel_ids"])
        self.assertEqual(len(row["department"]["channel_ids"]), 2)

    def test_channel_ids_correct_without_prefetch(self):
        """Fallback: serializer dùng một mình (endpoint khác) vẫn phải trả đúng."""
        user = User.objects.get(username="deptch_small_0")
        row = UserSerializer(user).data
        self.assertEqual(len(row["department"]["channel_ids"]), 2)


class RoleListNPlusOneTests(TestCase):
    """RoleSerializer đọc 4 quan hệ; thiếu prefetch là 1 query MỖI role MỖI quan hệ."""

    @classmethod
    def setUpTestData(cls):
        from apps.users.models import Permission

        # Permission có unique (section, action) và migration đã seed sẵn bộ chuẩn —
        # tạo mới "orders/view" sẽ vỡ constraint. Lấy lại bản đã seed.
        cls.permission = Permission.objects.filter(section="orders", action="view").first()
        if cls.permission is None:  # pragma: no cover - seed luôn có trong test DB
            cls.permission = Permission.objects.create(
                code="orders:view", section="orders", action="view"
            )

    def _make_roles(self, count: int, prefix: str) -> None:
        from apps.users.models import RoleFieldGrant, RoleFieldRule, RolePermission, RoleSectionScope

        for i in range(count):
            role = Role.objects.create(name=f"{prefix}_{i}", scope_default="own")
            RolePermission.objects.create(role=role, permission=self.permission)
            RoleSectionScope.objects.create(role=role, section="orders", scope="own")
            RoleFieldRule.objects.create(
                role=role, section="orders", field="phone", rule="hidden"
            )
            RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

    def _measure(self) -> tuple[int, list]:
        from apps.users.serializers import RoleSerializer
        from apps.users.services import RoleService

        # Đo qua RoleService.list_roles() — chính thứ endpoint dùng. Nếu test tự dựng
        # queryset thì xoá prefetch khỏi view vẫn xanh, tức test không bảo vệ gì.
        with CaptureQueriesContext(connection) as ctx:
            data = RoleSerializer(RoleService.list_roles(), many=True).data
        return len(ctx.captured_queries), data

    def test_role_list_query_count_does_not_grow_with_role_count(self):
        self._make_roles(2, "role_nplus1_small")
        small, small_data = self._measure()

        self._make_roles(10, "role_nplus1_large")
        large, large_data = self._measure()

        self.assertGreater(len(large_data), len(small_data))
        self.assertEqual(
            large,
            small,
            f"N+1: {len(small_data)} role = {small} query, "
            f"{len(large_data)} role = {large} query.",
        )

    def test_role_payload_shape_unchanged(self):
        """Đổi sang đọc prefetch cache không được làm đổi nội dung trả ra."""
        from apps.users.serializers import RoleSerializer

        self._make_roles(1, "role_shape")
        role = Role.objects.get(name="role_shape_0")
        data = RoleSerializer(role).data

        self.assertEqual(data["permission_codes"], [self.permission.code])
        self.assertEqual(data["section_scopes"], {"orders": "own"})
        self.assertEqual(
            data["field_rules"],
            [{"section": "orders", "field": "phone", "rule": "hidden"}],
        )
        self.assertEqual(data["field_grants"], ["phone"])
