"""Đăng ký section vào VALID_SECTIONS + PERMISSION_CATALOG và sync_permissions().

Thiếu VALID_SECTIONS thì superuser bị FE ẩn menu (allowed_sections =
list(VALID_SECTIONS)) và role không gán scope được — bài học salaries
audit 2026-07-14b, lặp lại với contact_flow 19/07.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import VALID_SECTIONS, Permission, Role, RoleFieldGrant, RoleSectionScope
from apps.users.permissions import ORDER_SECTIONS
from apps.users.rbac_catalog import ALL_ORDER_FIELDS, PERMISSION_CATALOG, sync_permissions
from apps.users.tests.helpers import grant_order_fields, make_admin, make_user_with_sections


class PermissionCatalogSectionsRegisteredTests(TestCase):
    """Bất biến chung thay cho việc vá từng section một: mọi section có trong
    PERMISSION_CATALOG (nguồn migration seed Permission) đều PHẢI có mặt trong
    VALID_SECTIONS (nguồn `UserSerializer.get_allowed_sections` trả cho superuser
    và RoleSectionScope/RoleFieldGrant dùng để validate). Thiếu 1 chiều thì API
    vẫn khoẻ (superuser short-circuit permission check) nhưng FE ẩn trang và route
    guard chặn — lỗi im lặng, chỉ lộ ra khi có người thử bấm vào trang.

    Đã xảy ra 3 lần: salaries (audit 2026-07-14b), contact_flow (2026-07-19),
    customer-explorer (whole-branch review khối 2B, 2026-08-20) — mỗi lần được vá
    bằng 1 test riêng cho đúng section đó, không chặn được lần thứ 4. Test này
    chặn CẢ LỚP lỗi, không chỉ 1 section.
    """

    def test_every_catalog_section_is_in_valid_sections(self):
        missing = sorted(set(PERMISSION_CATALOG) - set(VALID_SECTIONS))
        self.assertEqual(
            missing,
            [],
            f"Section {missing} có trong PERMISSION_CATALOG nhưng thiếu trong "
            "VALID_SECTIONS (apps/users/models.py). Superuser sẽ bị FE ẩn trang "
            "và role không gán scope được dù API vẫn hoạt động bình thường. "
            "Thêm section thiếu vào VALID_SECTIONS trong apps/users/models.py.",
        )


class OverviewReportSectionTests(TestCase):
    def test_section_registered(self):
        # Thiếu VALID_SECTIONS thì superuser bị FE ẩn menu (allowed_sections =
        # list(VALID_SECTIONS)) và role không gán scope được — bài học salaries /
        # contact_flow (audit 2026-07-14b, 2026-07-19).
        self.assertIn("overview-report", VALID_SECTIONS)
        self.assertIn("overview-report", PERMISSION_CATALOG)
        self.assertEqual(PERMISSION_CATALOG["overview-report"], ("view",))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="overview-report:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"overview-report:view"})


class ContactFlowSectionTests(TestCase):
    def test_section_registered(self):
        # Thiếu VALID_SECTIONS thì superuser bị FE ẩn menu (allowed_sections =
        # list(VALID_SECTIONS)) và role không gán scope được — bài học salaries
        # audit 2026-07-14b, lặp lại với contact_flow 19/07.
        self.assertIn("contact_flow", VALID_SECTIONS)
        self.assertIn("contact_flow", PERMISSION_CATALOG)
        self.assertEqual(PERMISSION_CATALOG["contact_flow"], ("view", "edit"))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="contact_flow:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"contact_flow:view", "contact_flow:edit"})


class CustomerExplorerSectionTests(TestCase):
    def test_section_registered(self):
        # Thiếu VALID_SECTIONS thì superuser bị FE ẩn menu (allowed_sections =
        # list(VALID_SECTIONS)) và role không gán scope được — bài học salaries /
        # contact_flow (audit 2026-07-14b, 2026-07-19), tái diễn với
        # customer-explorer (whole-branch review khối 2B, 2026-08-20).
        self.assertIn("customer-explorer", VALID_SECTIONS)
        self.assertIn("customer-explorer", PERMISSION_CATALOG)
        # "export" đã thu hồi (migration 0041) khi trang chuyển thành màn Hồ sơ 360:
        # hai tab bảng và Excel export không còn tồn tại để mà xuất.
        self.assertEqual(PERMISSION_CATALOG["customer-explorer"], ("view",))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="customer-explorer:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"customer-explorer:view"})


class ContactsSectionTests(TestCase):
    def test_section_registered(self):
        self.assertIn("contacts", VALID_SECTIONS)
        self.assertIn("contacts", PERMISSION_CATALOG)
        self.assertEqual(PERMISSION_CATALOG["contacts"], ("view", "edit", "export"))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="contacts:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"contacts:view", "contacts:edit", "contacts:export"})


class SaleAssignmentSectionTests(TestCase):
    def test_section_registered(self):
        # Thiếu VALID_SECTIONS thì superuser bị FE ẩn (allowed_sections =
        # list(VALID_SECTIONS)) và role không gán được rule — cùng vết salaries.
        self.assertIn("sale-assignment", VALID_SECTIONS)
        self.assertIn("sale-assignment", PERMISSION_CATALOG)
        # Cố ý KHÔNG có "view": đây là quyền hành vi, không phải trang. Khai "view"
        # chỉ mời gọi người sau gate nhầm bằng require_section.
        self.assertEqual(PERMISSION_CATALOG["sale-assignment"], ("edit",))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="sale-assignment:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"sale-assignment:edit"})


class LoyalCustomersSectionTests(TestCase):
    def test_edit_action_registered(self):
        # Endpoint ghi note trước đây gate bằng loyal-customers:view — ai xem được là
        # ghi được. Action edit là điều kiện để siết lại (xem Task 5).
        self.assertEqual(PERMISSION_CATALOG["loyal-customers"], ("view", "edit", "export"))

    def test_sync_creates_edit_permission(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="loyal-customers:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(
            codes,
            {"loyal-customers:view", "loyal-customers:edit", "loyal-customers:export"},
        )


class OutsideOrdersSectionRemovedTests(TestCase):
    """Section `outside-orders` đã bị gỡ (2026-08-11) — không còn trong catalog
    quyền, không role nào giữ được nó, và endpoint cũ trả 404."""

    def test_not_in_valid_sections(self):
        self.assertNotIn("outside-orders", VALID_SECTIONS)

    def test_not_in_order_sections(self):
        self.assertNotIn("outside-orders", ORDER_SECTIONS)

    def test_not_in_permission_catalog(self):
        # PERMISSION_CATALOG là nguồn sync_rbac_permissions dùng để TÁI TẠO
        # Permission — nếu còn key này, chạy management command sẽ hồi sinh quyền
        # ma cho một section đã gỡ (audit review Task 3, 2026-08-11).
        self.assertNotIn("outside-orders", PERMISSION_CATALOG)

    def test_no_permission_row_left(self):
        self.assertFalse(Permission.objects.filter(section="outside-orders").exists())
        self.assertFalse(RoleSectionScope.objects.filter(section="outside-orders").exists())

    def test_endpoint_gone(self):
        client = APIClient()
        client.force_authenticate(user=make_admin("admin-no-outside"))
        response = client.post("/api/orders/outside", {"limit": "10", "offset": "0"}, format="json")
        self.assertEqual(response.status_code, 404)


class RoleFieldGrantsApiTests(TestCase):
    """Task 3: đọc/ghi field_grants qua API role. Không có GET role đơn lẻ — chỉ
    GET /roles (list) và PATCH /roles/<id> (xem apps/users/urls.py)."""

    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("admin-field-grants")
        self.client.force_authenticate(user=self.admin)

    def test_role_list_returns_field_grants(self):
        role = Role.objects.create(name="Kế toán test")
        RoleFieldGrant.objects.create(role=role, section="orders", field="total_amount")
        RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

        res = self.client.get("/api/users/roles")

        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.json() if r["id"] == role.id)
        self.assertEqual(row["field_grants"], ["phone", "total_amount"])  # sắp xếp ổn định

    def test_update_role_replaces_field_grants(self):
        role = Role.objects.create(name="Kho test")
        RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

        res = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"field_grants": ["shipping_code", "weight"]},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            set(RoleFieldGrant.objects.filter(role=role).values_list("field", flat=True)),
            {"shipping_code", "weight"},
        )

    def test_update_role_rejects_unknown_field_name(self):
        """Chống hẳn lớp lỗi của cơ chế cũ: gõ sai tên field thì rule im lặng không chạy.

        Seed sẵn một grant HỢP LỆ (`phone`) trước khi PATCH: nếu tập ban đầu rỗng thì
        "không ghi gì vào DB" luôn đúng vô điều kiện, không khoá được thứ tự validate
        trước - ghi sau. Assert grant cũ còn nguyên mới thật sự chứng minh request lỗi
        không đụng vào DB (code review Task 3, 2026-08-17).
        """
        role = Role.objects.create(name="Role gõ sai")
        RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

        res = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"field_grants": ["cost_price"]},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("cost_price", str(res.json()))
        self.assertEqual(
            set(RoleFieldGrant.objects.filter(role=role).values_list("field", flat=True)),
            {"phone"},
        )

    def test_empty_field_grants_clears_all(self):
        role = Role.objects.create(name="Role bị thu quyền")
        RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

        res = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"field_grants": []},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(RoleFieldGrant.objects.filter(role=role).exists())


class OrderFieldCatalogEndpointTests(TestCase):
    """Task 4: catalog nhóm field đơn cho FE dựng UI cấu hình quyền sửa field."""

    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("admin-field-catalog")
        self.client.force_authenticate(user=self.admin)

    def test_order_field_catalog_endpoint(self):
        res = self.client.get("/api/users/order-field-catalog")

        self.assertEqual(res.status_code, 200)
        groups = res.json()["groups"]
        self.assertEqual([g["code"] for g in groups][:3], ["customer", "address", "products"])
        money = next(g for g in groups if g["code"] == "money")
        self.assertEqual(money["label"], "Tiền & chiết khấu")
        self.assertEqual(
            {f["name"] for f in money["fields"]},
            {
                "total_amount", "total_after_discount", "discount_amount",
                "booking_voucher_discount", "other_price", "customer_prepaid",
                "shipping_fee",
            },
        )
        self.assertTrue(all(f["label"] for g in groups for f in g["fields"]))


class UserPayloadEditableOrderFieldsTests(TestCase):
    """Task 8: FE khoá ô nhập theo field dựa vào payload user — cần
    `editable_order_fields` trả đúng grant hiện có (test qua /api/users/me, phản chiếu
    UserSerializer dùng trong _me_payload)."""

    def setUp(self):
        self.client = APIClient()

    def test_user_payload_exposes_editable_order_fields(self):
        staff = make_user_with_sections("staff-field-grants", ["orders"])
        grant_order_fields(staff, "phone")

        self.client.force_authenticate(user=staff)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["editable_order_fields"], ["phone"])

    def test_user_payload_editable_order_fields_empty_without_grant(self):
        staff = make_user_with_sections("staff-no-field-grants", ["orders"])

        self.client.force_authenticate(user=staff)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["editable_order_fields"], [])

    def test_superuser_gets_all_order_fields(self):
        admin = make_admin("admin-editable-order-fields")

        self.client.force_authenticate(user=admin)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()["editable_order_fields"]), ALL_ORDER_FIELDS)


class MePayloadMediaSectionsTests(TestCase):
    """`/api/users/me` (và /login, cùng `_me_payload`) phải giữ các section Media mà
    `UserSerializer` gộp thêm cho user đã được cấp quyền Media.

    Trước đây `_me_payload` tính lại `allowed_sections` từ `permission_codes` nên ghi
    đè mất phần Media: user thường có `media_role` đăng nhập vào không thấy trang Media
    nào, còn superuser không lộ lỗi vì nhận trọn VALID_SECTIONS (prod 05/09/2026).
    """

    MEDIA_SECTIONS = {
        "media-contents",
        "media-explore",
        "media-proposals",
        "media-reports",
        "media-taxonomy",
    }

    def setUp(self):
        self.client = APIClient()

    def test_me_keeps_media_sections_for_provisioned_user(self):
        staff = make_user_with_sections("media-staff-me", ["orders"])
        staff.media_role = "uploader"
        staff.save(update_fields=["media_role"])

        self.client.force_authenticate(user=staff)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        sections = set(res.json()["allowed_sections"])
        self.assertTrue(sections >= self.MEDIA_SECTIONS)
        self.assertIn("orders", sections)

    def test_me_keeps_media_sections_when_user_has_no_other_permission(self):
        staff = make_user_with_sections("media-only-me", [])
        staff.media_role = "viewer"
        staff.save(update_fields=["media_role"])

        self.client.force_authenticate(user=staff)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()["allowed_sections"]), self.MEDIA_SECTIONS)

    def test_me_has_no_media_sections_without_provisioning(self):
        staff = make_user_with_sections("media-none-me", ["orders"])

        self.client.force_authenticate(user=staff)
        res = self.client.get("/api/users/me")

        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.MEDIA_SECTIONS & set(res.json()["allowed_sections"]))
