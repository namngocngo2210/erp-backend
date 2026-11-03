import io
from datetime import timedelta

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.floating_pool_service import FloatingPoolService, mask_phone
from apps.orders.models import FloatingPoolConfig, FloatingPoolEntry, Order, OrderAssessment, OrderStatus
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections


def make_order(channel, customer, **kw):
    return Order.objects.create(
        channel=channel, customer=customer, name=kw.pop("name", "KH Test"),
        phone="0911000001", total_amount="0.00", show_orders=True, **kw,
    )


class FloatingPoolModelTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fp-ch", name="FP Channel")
        self.customer = Customer.objects.create(
            full_name="KH FP", phone_raw="0911000001", phone_normalized="0911000001"
        )

    def test_config_get_solo_creates_single_row(self):
        a = FloatingPoolConfig.get_solo()
        b = FloatingPoolConfig.get_solo()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(FloatingPoolConfig.objects.count(), 1)
        self.assertFalse(a.enabled)
        self.assertEqual(a.stale_days, 3)

    def test_only_one_floating_entry_per_order(self):
        order = make_order(self.channel, self.customer)
        FloatingPoolEntry.objects.create(order=order, float_count=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FloatingPoolEntry.objects.create(order=order, float_count=2)
        # picked entry cũ không chặn entry floating mới
        FloatingPoolEntry.objects.filter(order=order).update(status=FloatingPoolEntry.Status.PICKED)
        FloatingPoolEntry.objects.create(order=order, float_count=2)


class MaskPhoneTests(TestCase):
    def test_mask_standard_vn_phone(self):
        self.assertEqual(mask_phone("0935112234"), "0935***234")

    def test_mask_short_or_empty(self):
        self.assertEqual(mask_phone(""), "***")
        self.assertEqual(mask_phone("12345"), "***")
        self.assertEqual(mask_phone("12345678"), "***")  # 8 ký tự: dưới ngưỡng 9, che hết

    def test_get_config_returns_singleton(self):
        self.assertEqual(FloatingPoolService.get_config().pk, FloatingPoolService.get_config().pk)


class _SweepFixtureMixin(TestCase):
    """Fixture dùng chung cho FloatingPoolSweepTests + FloatingPoolRescueTests.

    Subclass đặt channel_code riêng để tránh đụng unique constraint khi 2
    class test cùng chạy (mirror pattern channel code ngắn theo file).
    """

    channel_code = "fp-sw"
    channel_name = "FP Sweep"
    customer_name = "KH Sweep"
    customer_phone = "0911000002"

    def setUp(self):
        self.channel = Channel.objects.create(code=self.channel_code, name=self.channel_name)
        self.customer = Customer.objects.create(
            full_name=self.customer_name,
            phone_raw=self.customer_phone,
            phone_normalized=self.customer_phone,
        )
        self.sale = make_user_with_sections("fp-sale", ["orders"])
        cfg = FloatingPoolConfig.get_solo()
        cfg.enabled = True
        cfg.stale_days = 3
        cfg.saved_status_codes = ["closed"]
        cfg.save()

    def _stale_order(self, **kw):
        """Đơn có sale, tạo 10 ngày trước, không assessment — đủ điều kiện thả nổi."""
        order = make_order(self.channel, self.customer, staff=self.sale, **kw)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=10))
        order.refresh_from_db()
        return order

    def _set_assessment(self, order, *, code=None, followup=None, updated_ago_days=None):
        a, _ = OrderAssessment.objects.get_or_create(order=order)
        OrderAssessment.objects.filter(pk=a.pk).update(
            sales_status_code=code,
            next_followup_at=followup,
            updated_at=timezone.now() - timedelta(days=updated_ago_days or 0),
        )


class FloatingPoolSweepTests(_SweepFixtureMixin):
    def test_disabled_config_skips(self):
        FloatingPoolConfig.objects.filter(pk=1).update(enabled=False)
        self.assertEqual(FloatingPoolService.sweep(), {"skipped": "disabled"})

    def test_stale_order_without_assessment_floats(self):
        order = self._stale_order()
        summary = FloatingPoolService.sweep()
        self.assertEqual(summary["created"], 1)
        entry = FloatingPoolEntry.objects.get(order=order)
        self.assertEqual(entry.status, FloatingPoolEntry.Status.FLOATING)
        self.assertEqual(entry.previous_staff_id, self.sale.id)
        self.assertEqual(entry.float_count, 1)

    def test_saved_status_or_recent_activity_rescues(self):
        for label, code, updated_ago_days in (
            ("saved_status", "closed", 10),
            ("recent_activity", "no_answer_1", 1),
        ):
            with self.subTest(label):
                order = self._stale_order()
                self._set_assessment(order, code=code, updated_ago_days=updated_ago_days)
                self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_future_followup_rescues_but_past_does_not(self):
        order = self._stale_order()
        self._set_assessment(
            order, code="no_answer_1", updated_ago_days=10,
            followup=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)
        self._set_assessment(
            order, code="no_answer_1", updated_ago_days=10,
            followup=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(FloatingPoolService.sweep()["created"], 1)

    def test_no_staff_or_cancelled_never_floats(self):
        no_staff = make_order(self.channel, self.customer)
        Order.objects.filter(pk=no_staff.pk).update(created_at=timezone.now() - timedelta(days=10))
        cancelled = self._stale_order(status=OrderStatus.CANCELLED)
        FloatingPoolService.sweep()
        self.assertFalse(FloatingPoolEntry.objects.filter(order__in=[no_staff, cancelled]).exists())

    def test_max_float_count_and_age_limit(self):
        order = self._stale_order()
        cfg = FloatingPoolConfig.get_solo()
        cfg.max_float_count = 2
        cfg.save()
        for _ in range(2):
            FloatingPoolEntry.objects.create(
                order=order, status=FloatingPoolEntry.Status.PICKED,
                picked_at=timezone.now() - timedelta(days=5), float_count=1,
            )
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)  # chạm N=2
        cfg.max_float_count = 10
        cfg.max_order_age_days = 5
        cfg.save()
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)  # đơn 10 ngày > Z=5

    def test_recent_pick_resets_clock(self):
        order = self._stale_order()
        FloatingPoolEntry.objects.create(
            order=order, status=FloatingPoolEntry.Status.PICKED,
            picked_at=timezone.now() - timedelta(days=1), picked_by=self.sale, float_count=1,
        )
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_sweep_idempotent(self):
        self._stale_order()
        self.assertEqual(FloatingPoolService.sweep()["created"], 1)
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_old_pick_plus_recent_removed_entry_still_floats(self):
        """Regression: exclude() đa điều kiện trên quan hệ nhiều dòng không bind
        cùng 1 row — entry REMOVED có picked_at mới không được che cho entry
        PICKED cũ."""
        order = self._stale_order()
        FloatingPoolEntry.objects.create(
            order=order, status=FloatingPoolEntry.Status.PICKED,
            picked_at=timezone.now() - timedelta(days=20), float_count=1,
        )
        FloatingPoolEntry.objects.create(
            order=order, status=FloatingPoolEntry.Status.REMOVED,
            picked_at=timezone.now() - timedelta(days=1), float_count=1,
        )
        self.assertEqual(FloatingPoolService.sweep()["created"], 1)

    def test_soft_deleted_order_never_floats(self):
        order = self._stale_order()
        Order.objects.filter(pk=order.pk).update(deleted_at=timezone.now())
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_cancelled_invoice_never_floats(self):
        order = self._stale_order()
        Order.objects.filter(pk=order.pk).update(invoice_status=2)
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_invoiced_order_never_floats(self):
        """Đơn đã lên hóa đơn KiotViet (invoice_code không rỗng) = đã chốt thật,
        mặc nhiên được cứu (spec §3.2) dù stale."""
        order = self._stale_order()
        Order.objects.filter(pk=order.pk).update(invoice_code="HD123")
        self.assertEqual(FloatingPoolService.sweep()["created"], 0)

    def test_second_round_increments_float_count(self):
        order = self._stale_order()
        FloatingPoolEntry.objects.create(
            order=order, status=FloatingPoolEntry.Status.PICKED,
            picked_at=timezone.now() - timedelta(days=10), float_count=1,
        )
        self.assertEqual(FloatingPoolService.sweep()["created"], 1)
        entry = FloatingPoolEntry.objects.get(order=order, status=FloatingPoolEntry.Status.FLOATING)
        self.assertEqual(entry.float_count, 2)


class FloatingPoolRescueTests(_SweepFixtureMixin):
    """_remove_rescued: gỡ entry FLOATING khi đơn được cứu hoặc bị hủy."""

    channel_code = "fp-rs"
    channel_name = "FP Rescue"
    customer_name = "KH Rescue"
    customer_phone = "0911000003"

    def _float(self, order):
        FloatingPoolService.sweep()
        return FloatingPoolEntry.objects.get(order=order, status=FloatingPoolEntry.Status.FLOATING)

    def test_rescued_by_saved_status(self):
        order = self._stale_order()
        entry = self._float(order)
        self._set_assessment(order, code="closed", updated_ago_days=10)
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)

    def test_rescued_by_new_activity(self):
        order = self._stale_order()
        entry = self._float(order)
        self._set_assessment(order, code="no_answer_1", updated_ago_days=0)  # updated_at = now > entered_at
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)

    def test_removed_when_order_cancelled(self):
        order = self._stale_order()
        entry = self._float(order)
        Order.objects.filter(pk=order.pk).update(status=OrderStatus.CANCELLED)
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.CANCELLED)

    def test_removed_when_order_soft_deleted(self):
        order = self._stale_order()
        entry = self._float(order)
        Order.objects.filter(pk=order.pk).update(deleted_at=timezone.now())
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.CANCELLED)

    def test_removed_when_invoice_cancelled(self):
        order = self._stale_order()
        entry = self._float(order)
        Order.objects.filter(pk=order.pk).update(invoice_status=2)
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.CANCELLED)

    def test_removed_when_invoice_code_appears(self):
        order = self._stale_order()
        entry = self._float(order)
        Order.objects.filter(pk=order.pk).update(invoice_code="HD999")
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)

    def test_removed_when_staff_changed(self):
        order = self._stale_order()
        entry = self._float(order)
        other_sale = make_user_with_sections("fp-sale-2", ["orders"])
        Order.objects.filter(pk=order.pk).update(staff=other_sale)
        FloatingPoolService.sweep()
        entry.refresh_from_db()
        self.assertEqual(entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)

    def test_saved_round_does_not_count_toward_limit(self):
        order = self._stale_order()
        self._float(order)
        self._set_assessment(order, code="closed", updated_ago_days=10)
        FloatingPoolService.sweep()  # gỡ saved
        self._set_assessment(order, code="no_answer_1", updated_ago_days=10)
        self.assertEqual(FloatingPoolService.sweep()["created"], 1)  # vẫn nổi lại được, float_count=1
        self.assertEqual(
            FloatingPoolEntry.objects.get(order=order, status="floating").float_count, 1
        )


class FloatingPoolClaimTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fp-cl", name="FP Claim")
        self.customer = Customer.objects.create(
            full_name="KH Claim", phone_raw="0911000003", phone_normalized="0911000003"
        )
        self.sale_a = make_user_with_sections("fp-a", ["orders"])
        self.sale_b = make_user_with_sections("fp-b", ["orders"])
        cfg = FloatingPoolConfig.get_solo()
        cfg.enabled = True
        cfg.daily_claim_limit = 2
        cfg.save()
        self.order = make_order(self.channel, self.customer, staff=self.sale_a)
        self.entry = FloatingPoolEntry.objects.create(
            order=self.order, previous_staff=self.sale_a, float_count=1
        )

    def test_claim_reassigns_order(self):
        from apps.orders.application.services.floating_pool_service import (
            ClaimConflict,
        )
        order = FloatingPoolService.claim(self.entry.id, self.sale_b)
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, FloatingPoolEntry.Status.PICKED)
        self.assertEqual(self.entry.picked_by_id, self.sale_b.id)
        self.assertIsNotNone(self.entry.picked_at)
        # nhặt lần 2 cùng entry -> conflict
        with self.assertRaises(ClaimConflict):
            FloatingPoolService.claim(self.entry.id, self.sale_b)

    def test_previous_staff_cannot_reclaim(self):
        from apps.orders.application.services.floating_pool_service import ClaimForbidden
        with self.assertRaises(ClaimForbidden):
            FloatingPoolService.claim(self.entry.id, self.sale_a)

    def test_daily_quota(self):
        from apps.orders.application.services.floating_pool_service import ClaimError
        for i in range(2):
            o = make_order(self.channel, self.customer, staff=self.sale_a, name=f"Q{i}")
            e = FloatingPoolEntry.objects.create(order=o, previous_staff=self.sale_a, float_count=1)
            FloatingPoolService.claim(e.id, self.sale_b)
        self.assertEqual(FloatingPoolService.claimed_today(self.sale_b), 2)
        with self.assertRaises(ClaimError):
            FloatingPoolService.claim(self.entry.id, self.sale_b)

    def test_claim_cancelled_order_conflicts_and_removes(self):
        from apps.orders.application.services.floating_pool_service import ClaimConflict
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.CANCELLED)
        with self.assertRaises(ClaimConflict):
            FloatingPoolService.claim(self.entry.id, self.sale_b)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.removed_reason, FloatingPoolEntry.RemovedReason.CANCELLED)

    def test_claim_invoiced_order_conflicts_and_removes_saved(self):
        from apps.orders.application.services.floating_pool_service import ClaimConflict
        Order.objects.filter(pk=self.order.pk).update(invoice_code="HD777")
        with self.assertRaises(ClaimConflict):
            FloatingPoolService.claim(self.entry.id, self.sale_b)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(self.entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)

    def test_claim_staff_drifted_order_conflicts_and_removes_saved(self):
        from apps.orders.application.services.floating_pool_service import ClaimConflict
        other_sale = make_user_with_sections("fp-c-drift", ["orders"])
        Order.objects.filter(pk=self.order.pk).update(staff=other_sale)
        with self.assertRaises(ClaimConflict):
            FloatingPoolService.claim(self.entry.id, self.sale_b)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, FloatingPoolEntry.Status.REMOVED)
        self.assertEqual(self.entry.removed_reason, FloatingPoolEntry.RemovedReason.SAVED)


class SweepCommandTests(TestCase):
    def test_command_runs(self):
        out = io.StringIO()
        call_command("sweep_floating_pool", stdout=out)
        self.assertIn("floating-pool sweep", out.getvalue())


class FloatingPoolPermissionTests(TestCase):
    def test_config_requires_floating_pool_view_permission(self):
        for username, perms, status in (
            ("fpool-viewer", ["floating-pool:view"], 200),
            ("fpool-noview", ["orders:view"], 403),
        ):
            with self.subTest(perms=perms):
                user = make_user_with_permissions(username, perms)
                client = APIClient()
                client.force_authenticate(user=user)
                resp = client.get("/api/orders/floating-pool/config")
                self.assertEqual(resp.status_code, status)


class FloatingPoolConfigApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer = make_user_with_permissions("fp-viewer2", ["floating-pool:view"])
        self.editor = make_user_with_permissions(
            "fp-editor", ["floating-pool:view", "floating-pool:edit"]
        )

    def test_get_returns_defaults_and_options(self):
        self.client.force_authenticate(user=self.viewer)
        resp = self.client.get("/api/orders/floating-pool/config")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["enabled"])
        self.assertEqual(resp.data["stale_days"], 3)
        codes = [o["code"] for o in resp.data["saved_status_options"]]
        self.assertIn("closed", codes)

    def test_put_requires_edit_permission(self):
        payload = {
            "enabled": True, "stale_days": 5, "saved_status_codes": ["closed"],
            "max_float_count": 10, "daily_claim_limit": 10,
            "max_order_age_days": 0, "hide_phone": True,
        }
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(
            self.client.put("/api/orders/floating-pool/config", payload, format="json").status_code,
            403,
        )
        self.client.force_authenticate(user=self.editor)
        resp = self.client.put("/api/orders/floating-pool/config", payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["stale_days"], 5)

    def test_put_rejects_unknown_status_code(self):
        self.client.force_authenticate(user=self.editor)
        payload = {
            "enabled": True, "stale_days": 5, "saved_status_codes": ["khong-ton-tai"],
            "max_float_count": 10, "daily_claim_limit": 10,
            "max_order_age_days": 0, "hide_phone": True,
        }
        self.assertEqual(
            self.client.put("/api/orders/floating-pool/config", payload, format="json").status_code,
            400,
        )


class FloatingPoolEntriesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer = make_user_with_permissions("fp-lviewer", ["floating-pool:view"])
        self.editor = make_user_with_permissions(
            "fp-leditor", ["floating-pool:view", "floating-pool:edit"]
        )
        self.channel = Channel.objects.create(code="fp-ls", name="FP List")
        self.customer = Customer.objects.create(
            full_name="KH List", phone_raw="0935112234", phone_normalized="0935112234"
        )
        self.sale = make_user_with_sections("fp-lsale", ["orders"])
        FloatingPoolConfig.objects.update_or_create(pk=1, defaults={"enabled": True, "hide_phone": True})
        self.order = make_order(self.channel, self.customer, name="Nhi Nhi")
        Order.objects.filter(pk=self.order.pk).update(staff=self.sale, phone="0935112234", phone_normalized="0935112234")
        self.entry = FloatingPoolEntry.objects.create(
            order=self.order, previous_staff=self.sale, float_count=1
        )

    def _get(self, user, params=""):
        self.client.force_authenticate(user=user)
        return self.client.get(f"/api/orders/floating-pool/entries{params}")

    def test_list_masks_phone_for_viewer(self):
        resp = self._get(self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total"], 1)
        item = resp.data["items"][0]
        self.assertEqual(item["phone"], "0935***234")
        self.assertEqual(item["customer_name"], "Nhi Nhi")
        self.assertEqual(item["float_count"], 1)

    def test_editor_sees_full_phone(self):
        self.assertEqual(self._get(self.editor).data["items"][0]["phone"], "0935112234")

    def test_search_name_partial_and_phone_exact_only(self):
        self.assertEqual(self._get(self.viewer, "?q=Nhi").data["total"], 1)
        self.assertEqual(self._get(self.viewer, "?q=093511").data["total"], 0)  # partial phone bị chặn
        # Viewer bị che số (hide_phone=True, không có quyền edit) → search exact phone
        # không được dùng để dò membership, luôn trả 0 dù đúng số.
        self.assertEqual(self._get(self.viewer, "?q=0935112234").data["total"], 0)

    def test_editor_can_search_exact_phone(self):
        # Editor thấy full số (show_full=True) → search exact phone hoạt động bình thường.
        self.assertEqual(self._get(self.editor, "?q=0935112234").data["total"], 1)

    def test_quota_fields_present(self):
        data = self._get(self.viewer).data
        self.assertEqual(data["claimed_today"], 0)
        self.assertEqual(data["daily_claim_limit"], FloatingPoolConfig.get_solo().daily_claim_limit)


class FloatingPoolClaimApiTests(TestCase):
    """Claim gate bằng floating-pool:edit (sweep N2 đợt 2). Fixture `claimer`
    mô phỏng role sale prod SAU grandfather 0039 (:view kèm :edit)."""

    def setUp(self):
        self.client = APIClient()
        self.claimer = make_user_with_permissions(
            "fp-claimer", ["floating-pool:view", "floating-pool:edit"]
        )
        # Trạng thái sau khi admin thu hồi :edit trên UI — xem được, không nhận được.
        self.viewer = make_user_with_permissions("fp-cviewer", ["floating-pool:view"])
        self.channel = Channel.objects.create(code="fp-ca", name="FP ClaimApi")
        self.customer = Customer.objects.create(
            full_name="KH CA", phone_raw="0911000004", phone_normalized="0911000004"
        )
        self.sale = make_user_with_sections("fp-csale", ["orders"])
        FloatingPoolConfig.objects.update_or_create(pk=1, defaults={"enabled": True})
        order = make_order(self.channel, self.customer)
        Order.objects.filter(pk=order.pk).update(staff=self.sale)
        self.entry = FloatingPoolEntry.objects.create(
            order=order, previous_staff=self.sale, float_count=1
        )

    def test_claim_ok_then_conflict(self):
        self.client.force_authenticate(user=self.claimer)
        resp = self.client.post(f"/api/orders/floating-pool/entries/{self.entry.id}/claim")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["claimed_today"], 1)
        resp2 = self.client.post(f"/api/orders/floating-pool/entries/{self.entry.id}/claim")
        self.assertEqual(resp2.status_code, 409)

    def test_claim_missing_entry_404(self):
        self.client.force_authenticate(user=self.claimer)
        self.assertEqual(
            self.client.post("/api/orders/floating-pool/entries/999999/claim").status_code, 404
        )

    def test_view_only_cannot_claim(self):
        """Contract mới: floating-pool:view trần không nhận số được nữa — đòn
        bẩy siết thật sau grandfather là thu hồi :edit per-role trên UI."""
        self.client.force_authenticate(user=self.viewer)
        resp = self.client.post(f"/api/orders/floating-pool/entries/{self.entry.id}/claim")
        self.assertEqual(resp.status_code, 403)
