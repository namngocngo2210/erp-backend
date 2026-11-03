from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Role, RolePermission, User, UserRoleAssignment
from apps.users.rbac_catalog import sync_permissions
from apps.users.serializers import UserSerializer
from apps.users.tests.helpers import make_admin, make_user_with_permissions


class RoleAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("adm")

    def test_non_admin_cannot_list_roles(self):
        staff = make_user_with_permissions("s", ["orders:view"])
        self.client.force_authenticate(user=staff)
        resp = self.client.get("/api/users/roles")
        self.assertEqual(resp.status_code, 403)

    def test_admin_lists_permissions_catalog(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/users/permissions")
        self.assertEqual(resp.status_code, 200)
        codes = {row["code"] for row in resp.json()}
        self.assertIn("orders:view", codes)

    def test_create_role_with_permissions(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            "/api/users/roles",
            {"name": "Sale Lead", "scope_default": "department",
             "permission_codes": ["orders:view", "orders:edit"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        role = Role.objects.get(name="Sale Lead")
        self.assertEqual(role.scope_default, "department")
        self.assertEqual(
            set(RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)),
            {"orders:view", "orders:edit"},
        )

    def test_update_role_permissions_replaces_set(self):
        self.client.force_authenticate(user=self.admin)
        role = Role.objects.create(name="Editor")
        resp = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"permission_codes": ["customers:view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)),
            {"customers:view"},
        )

    def test_cannot_edit_system_role(self):
        self.client.force_authenticate(user=self.admin)
        # Tên phải KHÁC "Administrator": migration 0015 seed sẵn role đó vào test DB
        # → create() trùng `rbac_roles_name_key`. Khóa sửa xét cờ is_system, không xét tên.
        sys_role = Role.objects.create(name="System Role (admin api test)", is_system=True)
        resp = self.client.patch(
            f"/api/users/roles/{sys_role.id}", {"name": "Hacked"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_assign_roles_to_user(self):
        self.client.force_authenticate(user=self.admin)
        target = User.objects.create_user(username="t", password="x")
        r1 = Role.objects.create(name="R1")
        r2 = Role.objects.create(name="R2")
        resp = self.client.patch(
            f"/api/users/admin/{target.id}", {"role_ids": [r1.id, r2.id]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(UserRoleAssignment.objects.filter(user=target).values_list("role_id", flat=True)),
            {r1.id, r2.id},
        )

    def test_create_user_with_role_ids(self):
        # role_ids phải được pop khỏi validated trước User(**validated), nếu không create 500.
        self.client.force_authenticate(user=self.admin)
        r1 = Role.objects.create(name="R1")
        resp = self.client.post(
            "/api/users/admin",
            {"username": "newbie", "password": "secret-pass-123", "role_ids": [r1.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        created = User.objects.get(username="newbie")
        self.assertEqual(
            set(UserRoleAssignment.objects.filter(user=created).values_list("role_id", flat=True)),
            {r1.id},
        )


class AdminUserZenifyEmailTests(TestCase):
    """Admin nhập "Email Zenify" cho từng user qua POST/PATCH /api/users/admin."""

    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("adm")
        self.client.force_authenticate(user=self.admin)

    def test_create_user_with_zenify_email(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "sale1", "password": "secret-pass-123", "zenify_email": " Sale1@zenify.vn "},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["zenify_email"], "Sale1@zenify.vn")
        self.assertEqual(User.objects.get(username="sale1").zenify_email, "Sale1@zenify.vn")

    def test_create_user_without_zenify_email_defaults_blank(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "sale2", "password": "secret-pass-123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["zenify_email"], "")

    def test_create_user_rejects_invalid_zenify_email(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "sale3", "password": "secret-pass-123", "zenify_email": "not-an-email"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("zenify_email", resp.json())

    def test_patch_updates_and_clears_zenify_email(self):
        user = User.objects.create(username="sale4")
        resp = self.client.patch(
            f"/api/users/admin/{user.id}", {"zenify_email": "sale4@zenify.vn"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["zenify_email"], "sale4@zenify.vn")
        user.refresh_from_db()
        self.assertEqual(user.zenify_email, "sale4@zenify.vn")

        # PATCH không gửi field → giữ nguyên.
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"display_name": "S4"}, format="json")
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.zenify_email, "sale4@zenify.vn")

        # Gửi chuỗi rỗng → gỡ liên kết.
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"zenify_email": ""}, format="json")
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.zenify_email, "")

    def test_create_user_with_foxia_id_trims_whitespace(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "fx1", "password": "secret-pass-123", "foxia_id": " 1234 "},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["foxia_id"], "1234")
        self.assertEqual(User.objects.get(username="fx1").foxia_id, "1234")

    def test_create_user_without_foxia_id_defaults_blank(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "fx2", "password": "secret-pass-123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["foxia_id"], "")

    def test_create_user_rejects_duplicate_foxia_id(self):
        User.objects.create(username="fx-owner", foxia_id="777")
        resp = self.client.post(
            "/api/users/admin",
            {"username": "fx3", "password": "secret-pass-123", "foxia_id": "777"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("foxia_id", resp.json())

    def test_duplicate_foxia_id_ignores_soft_deleted_user(self):
        from django.utils import timezone

        User.objects.create(username="fx-gone", foxia_id="888", deleted_at=timezone.now())
        resp = self.client.post(
            "/api/users/admin",
            {"username": "fx4", "password": "secret-pass-123", "foxia_id": "888"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_patch_updates_and_clears_foxia_id(self):
        user = User.objects.create(username="fx5")
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"foxia_id": "555"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["foxia_id"], "555")
        user.refresh_from_db()
        self.assertEqual(user.foxia_id, "555")

        # PATCH lại chính giá trị đang có → không bị coi là trùng với chính mình.
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"foxia_id": "555"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)

        # PATCH không gửi field → giữ nguyên.
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"display_name": "F5"}, format="json")
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.foxia_id, "555")

        # Trùng với user khác → 400.
        other = User.objects.create(username="fx6", foxia_id="666")
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"foxia_id": "666"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("foxia_id", resp.json())
        self.assertEqual(User.objects.get(id=other.id).foxia_id, "666")

        # Gửi chuỗi rỗng → gỡ liên kết.
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"foxia_id": ""}, format="json")
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.foxia_id, "")


class AdminUsersPaginationTests(TestCase):
    """GET /api/users/admin: phân trang limit/offset + lọc q (username/display_name)."""

    @classmethod
    def setUpTestData(cls):
        sync_permissions()
        cls.admin = make_admin("adm")
        User.objects.bulk_create(
            User(username=f"user{i:03d}", display_name=f"Nhân viên {i:03d}")
            for i in range(120)
        )
        User.objects.create(username="alice", display_name="Trần Thị B")
        User.objects.create(username="bob", display_name="Alice Nguyễn")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_response_shape_and_default_page_size(self):
        resp = self.client.get("/api/users/admin")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data.keys()), {"items", "total"})
        self.assertEqual(data["total"], 123)  # admin + 120 + alice + bob
        self.assertEqual(len(data["items"]), 20)

    def test_limit_offset_slice_ordered_by_id(self):
        resp = self.client.get("/api/users/admin", {"limit": 5, "offset": 10})
        data = resp.json()
        self.assertEqual(data["total"], 123)
        self.assertEqual(len(data["items"]), 5)
        ids = [row["id"] for row in data["items"]]
        self.assertEqual(ids, sorted(ids))
        all_ids = list(
            User.objects.filter(deleted_at__isnull=True)
            .order_by("id")
            .values_list("id", flat=True)
        )
        self.assertEqual(ids, all_ids[10:15])

    def test_q_matches_username_and_display_name_case_insensitive(self):
        resp = self.client.get("/api/users/admin", {"q": "ALICE"})
        data = resp.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(
            {row["username"] for row in data["items"]}, {"alice", "bob"}
        )

    def test_q_no_match_returns_empty(self):
        resp = self.client.get("/api/users/admin", {"q": "khong-ton-tai-xyz"})
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])

    def test_limit_clamped_to_100_and_garbage_params_fallback(self):
        resp = self.client.get("/api/users/admin", {"limit": 500})
        self.assertEqual(len(resp.json()["items"]), 100)
        resp = self.client.get("/api/users/admin", {"limit": "abc", "offset": "xyz"})
        data = resp.json()
        self.assertEqual(len(data["items"]), 20)
        self.assertEqual(data["items"][0]["username"], "adm")

    def test_non_admin_still_403(self):
        staff = make_user_with_permissions("staffx", ["users:view"])
        self.client.force_authenticate(user=staff)
        resp = self.client.get("/api/users/admin")
        self.assertEqual(resp.status_code, 403)


class AdminUsersMediaFieldsTests(TestCase):
    """Vai trò Media + API key Media riêng (spec Media §4.7)."""

    MEDIA_SECTIONS = {"media-explore", "media-contents", "media-proposals", "media-reports", "media-taxonomy"}

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("adm")
        self.client.force_authenticate(user=self.admin)

    def test_create_with_role_and_key_never_echoes_key(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m1", "password": "secret-pass-123", "media_role": "uploader",
             "media_api_key": "dak_abcdefghijklmnop"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["media_role"], "uploader")
        self.assertTrue(body["media_api_key_set"])
        self.assertEqual(body["media_api_key_prefix"], "dak_abcdefgh")
        self.assertNotIn("media_api_key", body)
        self.assertEqual(User.objects.get(username="m1").media_api_key, "dak_abcdefghijklmnop")
        self.assertTrue(set(body["allowed_sections"]) >= self.MEDIA_SECTIONS)

    def test_create_rejects_key_without_dak_prefix_and_bad_role(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m2", "password": "secret-pass-123", "media_api_key": "abc"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("media_api_key", resp.json())
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m3", "password": "secret-pass-123", "media_role": "boss"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("media_role", resp.json())

    def test_patch_blank_keeps_key_null_clears_key(self):
        user = User.objects.create(username="m4", media_api_key="dak_keepme12345", media_role="viewer")
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_api_key": ""}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        user.refresh_from_db()
        self.assertEqual(user.media_api_key, "dak_keepme12345")

        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_api_key": None}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        user.refresh_from_db()
        self.assertEqual(user.media_api_key, "")
        self.assertFalse(resp.json()["media_api_key_set"])
        # Vẫn provisioned nhờ media_role.
        self.assertTrue(set(resp.json()["allowed_sections"]) >= self.MEDIA_SECTIONS)

        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_role": ""}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.MEDIA_SECTIONS & set(resp.json()["allowed_sections"]))

    def test_unprovisioned_user_has_no_media_sections(self):
        staff = make_user_with_permissions("s1", ["orders:view"])
        self.assertFalse(self.MEDIA_SECTIONS & set(UserSerializer(staff).data["allowed_sections"]))


class AdminUserPasswordUpdateTests(TestCase):
    """PATCH /api/users/admin/<id> — sửa thông tin khác mà để trống mật khẩu mới
    thì KHÔNG được đụng vào mật khẩu hiện tại (không xoá, không đặt rỗng)."""

    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("adm")
        self.client.force_authenticate(user=self.admin)
        self.target = User.objects.create_user(username="staff1", password="old-pass-12345")

    def _reload(self):
        self.target.refresh_from_db()
        return self.target

    def test_patch_without_password_key_keeps_password(self):
        resp = self.client.patch(
            f"/api/users/admin/{self.target.id}", {"display_name": "Đổi tên"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        user = self._reload()
        self.assertEqual(user.display_name, "Đổi tên")
        self.assertTrue(user.check_password("old-pass-12345"))

    def test_patch_with_blank_password_keeps_password(self):
        old_hash = self.target.password
        resp = self.client.patch(
            f"/api/users/admin/{self.target.id}",
            {"display_name": "Đổi tên 2", "password": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        user = self._reload()
        self.assertEqual(user.display_name, "Đổi tên 2")
        self.assertEqual(user.password, old_hash)
        self.assertTrue(user.check_password("old-pass-12345"))

    def test_patch_with_whitespace_password_keeps_password(self):
        old_hash = self.target.password
        resp = self.client.patch(
            f"/api/users/admin/{self.target.id}", {"password": "   "}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._reload().password, old_hash)

    def test_patch_with_real_password_changes_it(self):
        resp = self.client.patch(
            f"/api/users/admin/{self.target.id}", {"password": "new-pass-54321"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        user = self._reload()
        self.assertTrue(user.check_password("new-pass-54321"))

    def test_patch_with_weak_password_returns_string_detail(self):
        # 10 ký tự < chính sách 12: 400 với `detail` là CHUỖI (FE bỏ qua detail dạng
        # list → người dùng chỉ thấy "HTTP 400"), mật khẩu cũ giữ nguyên.
        resp = self.client.patch(
            f"/api/users/admin/{self.target.id}", {"password": "short-1234"}, format="json"
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        detail = resp.json()["detail"]
        self.assertIsInstance(detail, str)
        self.assertIn("12", detail)
        self.assertTrue(self._reload().check_password("old-pass-12345"))


class ChangePasswordApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="self1", password="old-pass-12345")
        self.client.force_authenticate(user=self.user)

    def _post(self, **payload):
        return self.client.post("/api/users/change-password", payload, format="json")

    def test_weak_new_password_returns_string_detail(self):
        resp = self._post(
            current_password="old-pass-12345", new_password="short-1234", confirm_password="short-1234"
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIsInstance(resp.json()["detail"], str)

    def test_wrong_current_password(self):
        resp = self._post(
            current_password="wrong-pass-000", new_password="brand-new-pass-99",
            confirm_password="brand-new-pass-99",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["detail"], "Mật khẩu hiện tại không đúng.")

    def test_valid_change_rotates_password(self):
        resp = self._post(
            current_password="old-pass-12345", new_password="brand-new-pass-99",
            confirm_password="brand-new-pass-99",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("access", resp.json())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-pass-99"))
