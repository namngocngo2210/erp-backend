from __future__ import annotations

import importlib

from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.cache_utils import bump_channels_cache_version
from apps.channels_core.models import Channel, ChannelType
from apps.catalog.models import PriceBook
from apps.users.tests.helpers import make_admin, make_user_with_sections

_MIGRATION = importlib.import_module(
    "apps.channels_core.migrations.0020_channel_code_active_uniq"
)


class ChannelPriceBookApiTests(TestCase):
    """Test field price_book trên API kênh."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("channel-pb-admin"))
        self.channel = Channel.objects.create(code="pb-api", name="Kênh PB")
        self.web = PriceBook.objects.get(code="web")

    def test_channel_list_exposes_null_price_book(self):
        response = self.client.get("/api/channels")
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["code"] == "pb-api")
        self.assertIsNone(row["price_book"])

    def test_assign_price_book(self):
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"price_book_id": self.web.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["price_book"]["code"], "web")
        # Response được serialize từ instance đã mutate trong bộ nhớ — không chứng
        # minh được đã lưu DB (vd. quên "price_book" trong update_fields vẫn cho
        # response giống hệt). Re-query từ DB để khoá đúng hành vi persistence.
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.price_book_id, self.web.id)

    def test_clear_price_book(self):
        self.channel.price_book = self.web
        self.channel.save(update_fields=["price_book"])
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"price_book_id": None}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["price_book"])
        self.channel.refresh_from_db()
        self.assertIsNone(self.channel.price_book_id)

    def test_soft_deleted_price_book_not_exposed(self):
        self.channel.price_book = self.web
        self.channel.save(update_fields=["price_book"])
        self.web.deleted_at = timezone.now()
        self.web.save(update_fields=["deleted_at"])

        response = self.client.get("/api/channels")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["code"] == "pb-api")
        self.assertIsNone(row["price_book"])


class ChannelTypeApiTests(TestCase):
    """Loại kênh bán (Shopee/TikTok/Facebook/Web) — danh mục + field trên API kênh."""

    # 4 loại được seed bởi migration 0024, đúng thứ tự sort_order.
    SEEDED_CODES = ["shopee", "tiktok", "facebook", "web"]

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("channel-type-admin"))
        self.channel = Channel.objects.create(code="ct-api", name="Kênh CT")
        self.shopee = ChannelType.objects.get(code="shopee")
        # Cache của /api/channels/types KHÔNG khoá theo user (danh mục dùng chung),
        # mà Redis test là Redis thật, sống qua từng test lẫn từng lần chạy suite.
        # Bump version mỗi test để không đọc phải payload của test trước.
        bump_channels_cache_version()

    def test_migration_seeds_four_types(self):
        # Assert theo SUBSET + thứ tự tương đối: mở thêm loại kênh mới (Lazada...)
        # không được làm đỏ test này, chỉ xoá/đổi 4 loại gốc mới được.
        codes = list(
            ChannelType.objects.filter(code__in=self.SEEDED_CODES).values_list("code", flat=True)
        )
        self.assertEqual(codes, self.SEEDED_CODES)

    def test_list_channel_types(self):
        response = self.client.get("/api/channels/types")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual([row["code"] for row in payload], self.SEEDED_CODES)
        self.assertEqual(payload[0]["name"], "Shopee")

    def test_inactive_type_hidden_from_catalog(self):
        ChannelType.objects.filter(code="web").update(is_active=False)
        response = self.client.get("/api/channels/types")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("web", [row["code"] for row in response.json()])

    def test_channel_types_require_channels_or_orders_section(self):
        """Danh mục mở cho trang kênh + trang đơn (ô lọc "Loại kênh"), ngoài ra đóng."""
        self.client.force_authenticate(make_user_with_sections("ct-outsider", ["customers"]))
        response = self.client.get("/api/channels/types")
        self.assertEqual(response.status_code, 403)

    def test_channel_list_exposes_null_channel_type(self):
        response = self.client.get("/api/channels")
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["code"] == "ct-api")
        self.assertIsNone(row["channel_type"])

    def test_assign_channel_type(self):
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"channel_type_id": self.shopee.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["channel_type"]["code"], "shopee")
        # Cùng lý do như test price_book: response serialize từ instance đã mutate
        # trong bộ nhớ nên quên "channel_type" trong update_fields vẫn xanh giả.
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_type_id, self.shopee.id)

    def test_clear_channel_type(self):
        self.channel.channel_type = self.shopee
        self.channel.save(update_fields=["channel_type"])
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"channel_type_id": None}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["channel_type"])
        self.channel.refresh_from_db()
        self.assertIsNone(self.channel.channel_type_id)

    def test_patch_without_channel_type_keeps_current_value(self):
        """Sentinel _UNSET: PATCH field khác KHÔNG được âm thầm bỏ phân loại."""
        self.channel.channel_type = self.shopee
        self.channel.save(update_fields=["channel_type"])
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"owner": "Nam_ADS"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_type_id, self.shopee.id)

    def test_create_channel_with_channel_type(self):
        response = self.client.post(
            "/api/channels",
            {
                "code": "ct-new",
                "name": "Kênh mới",
                "landingpage_source": [],
                "channel_type_id": self.shopee.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["channel_type"]["code"], "shopee")
        self.assertEqual(Channel.objects.get(code="ct-new").channel_type_id, self.shopee.id)

    def test_inactive_type_rejected_on_write(self):
        ChannelType.objects.filter(code="web").update(is_active=False)
        web = ChannelType.objects.get(code="web")
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"channel_type_id": web.id}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_type_in_use_is_protected_from_delete(self):
        """PROTECT (không phải SET_NULL): xoá loại kênh đang dùng phải nổ, không
        được âm thầm bỏ phân loại hàng loạt kênh."""
        self.channel.channel_type = self.shopee
        self.channel.save(update_fields=["channel_type"])
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.shopee.delete()

    def test_channel_list_does_not_n_plus_1_on_channel_type(self):
        """select_related("channel_type"): thêm kênh không được thêm query."""
        Channel.objects.create(code="ct-a", name="A", channel_type=self.shopee)
        # Tạo kênh bằng ORM thẳng không bump version cache như view PATCH/POST;
        # thiếu bump thì lần GET sau ăn cache và đếm ra 0 query -> xanh giả.
        # Lần GET đầu còn nạp cache RBAC nên tốn thêm 1 query — hâm nóng rồi mới đo.
        bump_channels_cache_version()
        self.client.get("/api/channels")

        bump_channels_cache_version()
        with CaptureQueriesContext(connection) as one_extra:
            self.client.get("/api/channels")

        tiktok = ChannelType.objects.get(code="tiktok")
        Channel.objects.create(code="ct-b", name="B", channel_type=tiktok)
        Channel.objects.create(code="ct-c", name="C", channel_type=tiktok)
        bump_channels_cache_version()
        with CaptureQueriesContext(connection) as three_extra:
            self.client.get("/api/channels")

        self.assertEqual(len(three_extra.captured_queries), len(one_extra.captured_queries))


class ChannelTypeCrudApiTests(TestCase):
    """CRUD danh mục loại kênh: /api/channels/types (+ /types/<id>)."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("channel-type-crud-admin"))
        self.shopee = ChannelType.objects.get(code="shopee")
        # Cache danh mục dùng chung mọi user (xem ChannelTypeApiTests.setUp).
        bump_channels_cache_version()

    def test_create_channel_type(self):
        response = self.client.post(
            "/api/channels/types",
            {"code": "Lazada", "name": "Lazada", "sort_order": 50},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        # code chuẩn hoá về chữ thường để không sinh 2 dòng "Lazada"/"lazada".
        self.assertEqual(response.json()["code"], "lazada")
        self.assertTrue(ChannelType.objects.filter(code="lazada").exists())

    def test_create_rejects_duplicate_code(self):
        response = self.client.post(
            "/api/channels/types", {"code": "shopee", "name": "Shopee 2"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_update_name_and_sort_order(self):
        response = self.client.patch(
            f"/api/channels/types/{self.shopee.id}",
            {"name": "Shopee Mall", "sort_order": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.shopee.refresh_from_db()
        self.assertEqual(self.shopee.name, "Shopee Mall")
        self.assertEqual(self.shopee.sort_order, 5)

    def test_update_ignores_code(self):
        """`code` là khoá nghiệp vụ (báo cáo + reverse migration 0024) nên KHÔNG sửa được."""
        response = self.client.patch(
            f"/api/channels/types/{self.shopee.id}", {"code": "shopee-mall"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.shopee.refresh_from_db()
        self.assertEqual(self.shopee.code, "shopee")

    def test_toggle_inactive_hides_from_dropdown_but_keeps_row(self):
        self.client.patch(
            f"/api/channels/types/{self.shopee.id}", {"is_active": False}, format="json"
        )
        dropdown = self.client.get("/api/channels/types")
        self.assertNotIn("shopee", [row["code"] for row in dropdown.json()])

        management = self.client.get("/api/channels/types?include_inactive=true")
        self.assertIn("shopee", [row["code"] for row in management.json()])

    def test_delete_unused_type(self):
        lazada = ChannelType.objects.create(code="lazada", name="Lazada", sort_order=50)
        response = self.client.delete(f"/api/channels/types/{lazada.id}")
        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(ChannelType.objects.filter(id=lazada.id).exists())

    def test_delete_type_in_use_returns_400(self):
        Channel.objects.create(code="ct-crud-used", name="Đang dùng", channel_type=self.shopee)
        response = self.client.delete(f"/api/channels/types/{self.shopee.id}")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("1 kênh", response.json()["detail"])
        self.assertTrue(ChannelType.objects.filter(id=self.shopee.id).exists())

    def test_channel_count_exposed(self):
        Channel.objects.create(code="ct-crud-count", name="Đếm", channel_type=self.shopee)
        response = self.client.get("/api/channels/types")
        row = next(item for item in response.json() if item["code"] == "shopee")
        self.assertEqual(row["channel_count"], 1)

    def test_soft_deleted_channel_not_counted(self):
        Channel.objects.create(
            code="ct-crud-gone",
            name="Đã xoá",
            channel_type=self.shopee,
            deleted_at=timezone.now(),
        )
        response = self.client.get("/api/channels/types")
        row = next(item for item in response.json() if item["code"] == "shopee")
        self.assertEqual(row["channel_count"], 0)


class ChannelTypePermissionTests(TestCase):
    """Gate theo action: đọc danh mục mở cho trang đơn, ghi thì phải có quyền kênh."""

    def setUp(self):
        self.client = APIClient()
        bump_channels_cache_version()
        self.shopee = ChannelType.objects.get(code="shopee")

    def test_orders_staff_can_read_catalog(self):
        """Ô lọc "Loại kênh" nằm trên trang đơn — sale không có `channels:view`
        vẫn phải nạp được danh mục, nếu không ô lọc rỗng trơn."""
        self.client.force_authenticate(make_user_with_sections("ct-sale", ["orders"]))
        response = self.client.get("/api/channels/types")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("shopee", [row["code"] for row in response.json()])

    def test_orders_staff_cannot_widen_to_inactive(self):
        ChannelType.objects.filter(code="web").update(is_active=False)
        self.client.force_authenticate(make_user_with_sections("ct-sale-2", ["orders"]))
        response = self.client.get("/api/channels/types?include_inactive=true")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("web", [row["code"] for row in response.json()])

    def test_orders_staff_cannot_write(self):
        self.client.force_authenticate(make_user_with_sections("ct-sale-3", ["orders"]))
        self.assertEqual(
            self.client.post(
                "/api/channels/types", {"code": "x", "name": "X"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/channels/types/{self.shopee.id}", {"name": "X"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/channels/types/{self.shopee.id}").status_code, 403
        )


class ChannelCodeUniqueTests(TestCase):
    """Tests partial unique Channel.code (audit hardening 2026-07 mục 3.6)."""

    def test_duplicate_active_code_rejected(self):
        Channel.objects.create(code="dupcode", name="A")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Channel.objects.create(code="dupcode", name="B")

    def test_duplicate_allowed_when_soft_deleted(self):
        Channel.objects.create(code="reuse", name="Old", deleted_at=timezone.now())
        Channel.objects.create(code="reuse", name="New")

    def test_migration_check_raises_on_duplicates(self):
        # Gỡ constraint trong transaction của test để dựng được trạng thái trùng.
        with connection.cursor() as cur:
            cur.execute(
                "ALTER TABLE channels DROP CONSTRAINT IF EXISTS channel_code_active_uniq"
            )
            cur.execute("DROP INDEX IF EXISTS channel_code_active_uniq")
        Channel.objects.create(code="dup2", name="A")
        Channel.objects.create(code="dup2", name="B")

        with self.assertRaises(RuntimeError) as ctx:
            _MIGRATION.check_duplicate_codes(django_apps, None)
        self.assertIn("dup2", str(ctx.exception))

    def test_migration_check_passes_when_clean(self):
        Channel.objects.create(code="cleancode", name="A")
        _MIGRATION.check_duplicate_codes(django_apps, None)
