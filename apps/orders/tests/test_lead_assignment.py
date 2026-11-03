"""Test chia đơn (lead assignment): API auto-assign/matrix + sweep + copy cấu hình."""
from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.application.services.shift_service import ShiftService
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.models import LeadAssignmentShare, Order, ShiftWindow
from apps.orders.sheet_rules import classify_sheet, sheet_q
from apps.orders.tests.helpers import ingest_headers
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import make_admin


class LandingIngestAutoAssignTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="fb-team", name="FB Team", owner="FB Team")
        self.sale = User.objects.create(username="sale_x")

    def _post_ingest(self, phone, address):
        payload = {
            "channel_id": "fb-team",
            "name": "Nguyen Van A",
            "phone": phone,
            "address": address,
            "order_source": "landing",
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", payload, format="json", **ingest_headers())

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_complete_landing_assigns_sheet1_staff(self, _shift):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.sale, weight=1
        )
        resp = self._post_ingest("0909123456", "1 Nguyen Trai, Q1")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123456")
        self.assertEqual(order.staff_id, self.sale.id)

    def test_incomplete_uses_sheet3_not_sheet1(self):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.sale, weight=1
        )
        resp = self._post_ingest("0909123457", "")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123457")
        self.assertIsNone(order.staff_id)

    def test_no_config_leaves_null(self):
        resp = self._post_ingest("0909123458", "1 Nguyen Trai, Q1")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123458")
        self.assertIsNone(order.staff_id)


class OvernightIngestConfigDateTests(TestCase):
    """Ca sáng cuộn qua đêm (21:30→11:59): đơn vào 21:30→23:59 ăn config NGÀY HÔM SAU."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="fb-night", name="FB Night", owner="FB Night")
        self.sale_today = User.objects.create(username="sale_today")
        self.sale_tomorrow = User.objects.create(username="sale_tomorrow")
        ShiftWindow.objects.all().delete()
        ShiftWindow.objects.create(
            shift="morning", start_time=time(21, 30), end_time=time(11, 59)
        )
        ShiftService.invalidate_cache()
        self.addCleanup(ShiftService.invalidate_cache)
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1",
            user=self.sale_today, weight=1,
        )
        LeadAssignmentShare.objects.create(
            date=timezone.localdate() + timedelta(days=1), shift="morning", sheet="sheet1",
            user=self.sale_tomorrow, weight=1,
        )

    def _post_ingest(self, phone):
        payload = {
            "channel_id": "fb-night",
            "name": "Nguyen Van Dem",
            "phone": phone,
            "address": "1 Nguyen Trai, Q1",
            "order_source": "landing",
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", payload, format="json", **ingest_headers())

    def _ingest_at(self, hour, phone):
        fake_now = timezone.localtime().replace(hour=hour, minute=0, second=0, microsecond=0)
        with patch(
            "apps.orders.application.services.shift_service.timezone.localtime",
            return_value=fake_now,
        ):
            resp = self._post_ingest(phone)
        self.assertEqual(resp.status_code, 200, resp.content)
        return Order.objects.get(phone=phone)

    def test_pre_midnight_uses_next_day_config(self):
        order = self._ingest_at(22, "0909900001")
        self.assertEqual(order.staff_id, self.sale_tomorrow.id)

    def test_post_midnight_uses_same_day_config(self):
        order = self._ingest_at(9, "0909900002")
        self.assertEqual(order.staff_id, self.sale_today.id)


class LarkIngestAutoAssignTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="lark-ch", name="Lark Ch", owner="Lark Ch")
        self.explicit = User.objects.create(username="explicit_sale")
        self.pool = User.objects.create(username="pool_sale")
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.pool, weight=1
        )

    def _post_lark(self, body):
        return self.client.post("/api/orders/lark-ingest", body, format="json", **ingest_headers())

    def test_lark_respects_explicit_staff_username(self):
        body = {
            "channel_code": "lark-ch",
            "name": "Tran B",
            "phone": "0911222333",
            "raw_address": "2 Le Loi",
            "order_source": "landing",
            "staff_username": "explicit_sale",
        }
        resp = self._post_lark(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222333")
        self.assertEqual(order.staff_id, self.explicit.id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_lark_auto_assigns_when_no_staff_username(self, _shift):
        body = {
            "channel_code": "lark-ch",
            "name": "Tran C",
            "phone": "0911222334",
            "raw_address": "3 Le Loi",
            "order_source": "landing",
        }
        resp = self._post_lark(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222334")
        self.assertEqual(order.staff_id, self.pool.id)


class LarkIngestFoxiaPushOrderingTests(TransactionTestCase):
    """Đẩy Foxia (on_commit) phải xếp lịch SAU khi bộ chia gán sale — task đọc lại
    đơn để gửi `staff_id`. TransactionTestCase: on_commit chạy thật ở
    lần commit ngoài cùng, không bị gom tới cuối test như TestCase."""

    def setUp(self):
        self.client = APIClient()
        Channel.objects.create(code="lark-ch", name="Lark Ch", owner="Lark Ch")
        self.pool = User.objects.create(username="pool_sale", foxia_id="321")
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.pool, weight=1
        )

    @override_settings(FOXIA_PUSH_ENABLED=True)
    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_lark_enqueue_foxia_sau_khi_da_chia_sale(self, _shift):
        staff_at_enqueue = []

        def _dispatch(task_name, **kwargs):
            if task_name == "foxia.push_order":
                staff_at_enqueue.append(Order.objects.get(pk=kwargs["order_id"]).staff_id)

        body = {
            "channel_code": "lark-ch",
            "name": "Tran D",
            "phone": "0911222335",
            "raw_address": "4 Le Loi",
            "order_source": "landing",
        }
        with patch("apps.dispatch.dispatch", side_effect=_dispatch):
            resp = self.client.post("/api/orders/lark-ingest", body, format="json", **ingest_headers())
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(staff_at_enqueue, [self.pool.id])


class SharesMatrixApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin1")
        self.client.force_authenticate(self.admin)
        self.today = timezone.localdate().isoformat()
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.u1 = User.objects.create(
            username="s1", department_id=DepartmentID.SALES, is_active=True
        )
        self.u2 = User.objects.create(
            username="s2", department_id=DepartmentID.SALES, is_active=True
        )

    def test_put_then_get_matrix_roundtrip(self):
        resp = self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "shift": "morning", "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 50, "sheet2": 0, "sheet3": 20}},
                {"user_id": self.u2.id, "weights": {"sheet1": 50, "sheet2": 100, "sheet3": 80}},
            ]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(
            LeadAssignmentShare.objects.filter(
                date=timezone.localdate(), sheet="sheet2", user=self.u1
            ).exists()
        )
        body = self.client.get(
            f"/api/orders/lead-assignment/shares?date={self.today}&shift=morning"
        ).json()
        self.assertEqual({s["key"] for s in body["sheets"]}, {"sheet1", "sheet2", "sheet3", "resale"})
        row_u1 = next(r for r in body["rows"] if r["user_id"] == self.u1.id)
        self.assertEqual(row_u1["cells"]["sheet1"]["weight"], 50)
        self.assertEqual(row_u1["cells"]["sheet1"]["target_pct"], 50.0)
        self.assertEqual(row_u1["cells"]["sheet2"]["weight"], 0)

    def test_put_keeps_assigned_count(self):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1",
            user=self.u1, weight=3, assigned_count=4
        )
        self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "shift": "morning", "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 9, "sheet2": 0, "sheet3": 0}},
            ]},
            format="json",
        )
        share = LeadAssignmentShare.objects.get(date=timezone.localdate(), sheet="sheet1", user=self.u1)
        self.assertEqual(share.weight, 9)
        self.assertEqual(share.assigned_count, 4)

    def test_delete_clears_whole_day_all_shifts(self):
        # Cấu hình cả 3 ca trong ngày → DELETE phải xoá sạch cả ngày.
        for sh in ("morning", "afternoon", "evening"):
            LeadAssignmentShare.objects.create(
                date=timezone.localdate(), shift=sh, sheet="sheet1", user=self.u1, weight=5
            )
        resp = self.client.delete(f"/api/orders/lead-assignment/shares?date={self.today}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(
            LeadAssignmentShare.objects.filter(date=timezone.localdate()).exists()
        )

    def test_delete_does_not_touch_other_dates(self):
        from datetime import timedelta
        other = timezone.localdate() - timedelta(days=1)
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1", user=self.u1, weight=5
        )
        LeadAssignmentShare.objects.create(
            date=other, shift="morning", sheet="sheet1", user=self.u1, weight=5
        )
        resp = self.client.delete(f"/api/orders/lead-assignment/shares?date={self.today}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(LeadAssignmentShare.objects.filter(date=timezone.localdate()).exists())
        self.assertTrue(LeadAssignmentShare.objects.filter(date=other).exists())

    def test_put_rejects_duplicate_user(self):
        resp = self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 1}},
                {"user_id": self.u1.id, "weights": {"sheet1": 2}},
            ]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_removes_users_absent_from_payload(self):
        from apps.orders.models import LeadAssignmentShare as _Share
        _Share.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1", user=self.u1, weight=5
        )
        _Share.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1", user=self.u2, weight=5
        )
        # PUT with only u1 present → u2's shares for the day must be deleted.
        resp = self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "shift": "morning", "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 10, "sheet2": 0, "sheet3": 0}},
            ]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(
            _Share.objects.filter(date=timezone.localdate(), user=self.u2).exists()
        )
        self.assertTrue(
            _Share.objects.filter(date=timezone.localdate(), sheet="sheet1", user=self.u1).exists()
        )


class ResaleSheetApiTests(TestCase):
    """Cột `resale` của ma trận: chỉ nhận người có Foxia ID dạng số (cơ hội resale
    gán sale bên Foxia bằng id đó — docs/foxia-resale-delivered.md)."""

    URL = "/api/orders/lead-assignment/shares"

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("admin_resale"))
        self.today = timezone.localdate().isoformat()
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.with_id = User.objects.create(
            username="rs1", department_id=DepartmentID.SALES, is_active=True, foxia_id="77"
        )
        self.without_id = User.objects.create(
            username="rs2", department_id=DepartmentID.SALES, is_active=True, foxia_id="abc"
        )

    def _put(self, user, weights):
        return self.client.put(
            self.URL,
            {"date": self.today, "shift": "morning", "rows": [{"user_id": user.id, "weights": weights}]},
            format="json",
        )

    def test_luu_va_doc_cot_resale(self):
        resp = self._put(self.with_id, {"sheet1": 0, "resale": 100})
        self.assertEqual(resp.status_code, 200, resp.content)
        row = next(r for r in resp.json()["rows"] if r["user_id"] == self.with_id.id)
        self.assertEqual(row["cells"]["resale"]["weight"], 100)
        self.assertTrue(
            LeadAssignmentShare.objects.filter(sheet="resale", user=self.with_id, weight=100).exists()
        )

    def test_chan_nguoi_khong_co_foxia_id_o_cot_resale(self):
        resp = self._put(self.without_id, {"resale": 50})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rs2", str(resp.content.decode()))
        self.assertFalse(LeadAssignmentShare.objects.filter(sheet="resale").exists())

    def test_nguoi_khong_co_foxia_id_van_nhan_sheet_sale(self):
        resp = self._put(self.without_id, {"sheet1": 50, "resale": 0})
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_staff_tra_co_foxia_id(self):
        items = {i["username"]: i for i in self.client.get("/api/orders/lead-assignment/staff").json()}
        self.assertTrue(items["rs1"]["has_foxia_id"])
        self.assertFalse(items["rs2"]["has_foxia_id"])

    def test_workload_khong_nhan_cot_resale(self):
        resp = self.client.get("/api/orders/lead-assignment/workload?sheet=resale")
        self.assertEqual(resp.status_code, 400)


class WorkloadTransferApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin2")
        self.client.force_authenticate(self.admin)
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.a = User.objects.create(
            username="wa", department_id=DepartmentID.SALES, is_active=True
        )
        self.b = User.objects.create(
            username="wb", department_id=DepartmentID.SALES, is_active=True
        )
        self.channel = Channel.objects.create(code="c1", name="C1", owner="C1")
        for i in range(3):
            Order.objects.create(
                channel=self.channel,
                customer=self._customer(f"099900{i}"),
                name="KH",
                phone=f"099900{i}",
                raw_address="HN",
                order_source="landing",
                staff=self.a,
            )

    def _customer(self, phone):
        from apps.customers.models import Customer
        return Customer.objects.create(full_name="KH", phone_raw=phone, phone_normalized=phone)

    def test_workload_counts_pending(self):
        body = self.client.get("/api/orders/lead-assignment/workload?sheet=sheet1").json()
        item = next(it for it in body["items"] if it["user_id"] == self.a.id)
        self.assertEqual(item["pending_count"], 3)

    def test_workload_filters_by_date(self):
        # Dời 1 đơn về hôm qua → hôm nay còn 2, hôm qua có 1.
        from datetime import timedelta
        yesterday = timezone.localdate() - timedelta(days=1)
        oldest = Order.objects.filter(staff=self.a).order_by("created_at").first()
        Order.objects.filter(pk=oldest.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        today_body = self.client.get(
            f"/api/orders/lead-assignment/workload?sheet=sheet1&date={timezone.localdate()}"
        ).json()
        self.assertEqual(
            next(it for it in today_body["items"] if it["user_id"] == self.a.id)["pending_count"], 2
        )
        y_body = self.client.get(
            f"/api/orders/lead-assignment/workload?sheet=sheet1&date={yesterday}"
        ).json()
        self.assertEqual(
            next(it for it in y_body["items"] if it["user_id"] == self.a.id)["pending_count"], 1
        )

    def test_transfer_moves_fifo(self):
        resp = self.client.post(
            "/api/orders/lead-assignment/transfer",
            {"sheet": "sheet1", "from_user_id": self.a.id, "to_user_id": self.b.id, "count": 2},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["transferred_count"], 2)
        self.assertEqual(Order.objects.filter(staff=self.b).count(), 2)
        self.assertEqual(Order.objects.filter(staff=self.a).count(), 1)

    def test_transfer_rejects_unknown_target(self):
        resp = self.client.post(
            "/api/orders/lead-assignment/transfer",
            {"sheet": "sheet1", "from_user_id": self.a.id, "to_user_id": 99999999, "count": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


# ─────────────────────────────────────────────────────────────────────────────
# Test sweep chia lại đơn sót + endpoint copy cấu hình chia đơn từ ngày gần nhất.
# ─────────────────────────────────────────────────────────────────────────────

_SHIFT_PATH = (
    "apps.orders.application.services.lead_assignment_service."
    "ShiftService.current_shift_and_date"
)


def _channel():
    channel, _ = Channel.objects.get_or_create(
        code="sweep-ch", defaults={"name": "Sweep", "owner": "Sweep"}
    )
    return channel


def _make_order(**overrides):
    from apps.customers.models import Customer

    phone = overrides.get("phone", "0909000001")
    customer = Customer.objects.create(
        full_name="KH", phone_raw=phone, phone_normalized=phone
    )
    defaults = dict(
        channel=_channel(),
        customer=customer,
        name="Nguyen Van A",
        phone=phone,
        raw_address="1 Nguyen Trai, Q1",
        order_source="landing",
    )
    defaults.update(overrides)
    return Order.objects.create(**defaults)


class SweepUnassignedTests(TestCase):
    def setUp(self):
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.sale = User.objects.create(
            username="sweep_sale", department_id=DepartmentID.SALES, is_active=True
        )
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet1",
            user=self.sale, weight=1,
        )

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_sweep_assigns_missed_order(self, _shift):
        order = _make_order()
        summary = LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertEqual(order.staff_id, self.sale.id)
        self.assertEqual(summary["assigned"], 1)

    @patch(_SHIFT_PATH, side_effect=lambda: (None, timezone.localdate()))
    def test_sweep_outside_working_hours_is_noop(self, _shift):
        order = _make_order(phone="0909000002")
        summary = LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertIsNone(order.staff_id)
        self.assertEqual(summary.get("skipped"), "outside_working_hours")

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_sweep_ignores_orders_older_than_lookback(self, _shift):
        order = _make_order(phone="0909000003")
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        summary = LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertIsNone(order.staff_id)
        self.assertEqual(summary["assigned"], 0)

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_sweep_ignores_non_sheet_orders(self, _shift):
        order = _make_order(phone="0909000004", order_source="web")
        summary = LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertIsNone(order.staff_id)
        self.assertEqual(summary["scanned"], 0)

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_sweep_does_not_touch_assigned_orders(self, _shift):
        other = User.objects.create(
            username="sweep_other", department_id=DepartmentID.SALES, is_active=True
        )
        order = _make_order(phone="0909000005", staff=other)
        LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertEqual(order.staff_id, other.id)
        share = LeadAssignmentShare.objects.get(user=self.sale)
        self.assertEqual(share.assigned_count, 0)

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_sweep_assigns_incomplete_order_via_sheet3(self, _shift):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), shift="morning", sheet="sheet3",
            user=self.sale, weight=1,
        )
        order = _make_order(phone="0909000006", raw_address="", address="")
        summary = LeadAssignmentService.sweep_unassigned()
        order.refresh_from_db()
        self.assertEqual(order.staff_id, self.sale.id)
        self.assertEqual(summary["assigned"], 1)


class CopySharesApiTests(TestCase):
    URL = "/api/orders/lead-assignment/shares/copy"

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("copy_admin")
        self.client.force_authenticate(self.admin)
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.u1 = User.objects.create(
            username="copy_s1", department_id=DepartmentID.SALES, is_active=True
        )
        self.u2 = User.objects.create(
            username="copy_s2", department_id=DepartmentID.SALES, is_active=True
        )
        self.today = timezone.localdate()

    def _seed_source(self, date):
        LeadAssignmentShare.objects.create(
            date=date, shift="morning", sheet="sheet1", user=self.u1, weight=60,
            assigned_count=7,
        )
        LeadAssignmentShare.objects.create(
            date=date, shift="morning", sheet="sheet1", user=self.u2, weight=40,
            assigned_count=3,
        )
        LeadAssignmentShare.objects.create(
            date=date, shift="evening", sheet="sheet2", user=self.u1, weight=100,
            assigned_count=1,
        )

    def test_copy_from_latest_configured_date(self):
        self._seed_source(self.today - timedelta(days=3))
        # Ngày cũ hơn nữa không được chọn làm nguồn.
        LeadAssignmentShare.objects.create(
            date=self.today - timedelta(days=5), shift="morning", sheet="sheet1",
            user=self.u2, weight=99,
        )
        resp = self.client.post(self.URL, {"date": self.today.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["source_date"], (self.today - timedelta(days=3)).isoformat())
        self.assertEqual(resp.data["copied"], 3)
        rows = LeadAssignmentShare.objects.filter(date=self.today)
        self.assertEqual(rows.count(), 3)
        for r in rows:
            self.assertEqual(r.assigned_count, 0)
        w = rows.get(shift="morning", sheet="sheet1", user=self.u1).weight
        self.assertEqual(w, 60)

    def test_copy_defaults_to_today(self):
        self._seed_source(self.today - timedelta(days=1))
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LeadAssignmentShare.objects.filter(date=self.today).count(), 3)

    def test_copy_blocked_when_target_already_configured(self):
        self._seed_source(self.today - timedelta(days=1))
        LeadAssignmentShare.objects.create(
            date=self.today, shift="morning", sheet="sheet1", user=self.u1, weight=1
        )
        resp = self.client.post(self.URL, {"date": self.today.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LeadAssignmentShare.objects.filter(date=self.today).count(), 1)

    def test_copy_without_source_returns_400(self):
        resp = self.client.post(self.URL, {"date": self.today.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_copy_skips_users_no_longer_active_sales(self):
        self._seed_source(self.today - timedelta(days=1))
        self.u2.is_active = False
        self.u2.save(update_fields=["is_active"])
        resp = self.client.post(self.URL, {"date": self.today.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["copied"], 2)
        self.assertFalse(
            LeadAssignmentShare.objects.filter(date=self.today, user=self.u2).exists()
        )


class ConsultingStickyRuleTests(TestCase):
    """Luật Tư vấn dính (2026-08-11): đơn đổ về KHÔNG có địa chỉ thuộc Sheet 3
    vĩnh viễn, kể cả sau khi sale điền đủ Tên/SĐT/Địa chỉ — và KHÔNG lọt sang
    Sheet 1/2. Đây là luật dùng chung cho bộ chia đơn, list view và báo cáo."""

    def setUp(self):
        self.channel = Channel.objects.create(code="ch-sticky", name="Sticky", owner="Sticky")
        self.customer = Customer.objects.create(
            full_name="KH Sticky", phone_raw="0909777001", phone_normalized="0909777001"
        )

    def _order(self, **kwargs):
        defaults = {
            "channel": self.channel,
            "customer": self.customer,
            "order_source": "landing",
            "name": "Nguyen A",
            "phone": "0909777001",
            "raw_address": "1 Nguyen Trai, Q1",
        }
        return Order.objects.create(**{**defaults, **kwargs})

    def test_classify_no_ingest_address_stays_sheet3_even_when_complete(self):
        sheet = classify_sheet(
            order_source="landing",
            name="Nguyen A",
            phone="0909777001",
            raw_address="1 Nguyen Trai, Q1",
            has_address=False,
        )
        self.assertEqual(sheet, "sheet3")

    def test_classify_messenger_no_ingest_address_is_sheet3_not_sheet2(self):
        sheet = classify_sheet(
            order_source="messenger",
            name="Nguyen A",
            phone="0909777001",
            raw_address="1 Nguyen Trai, Q1",
            has_address=False,
        )
        self.assertEqual(sheet, "sheet3")

    def test_classify_defaults_has_address_true_keeps_old_behaviour(self):
        # Không truyền has_address → đơn cũ (trước migration 0094) giữ hành vi cũ.
        self.assertEqual(
            classify_sheet(
                order_source="landing",
                name="Nguyen A",
                phone="0909777001",
                raw_address="1 Nguyen Trai, Q1",
            ),
            "sheet1",
        )

    def test_sheet_q_sticky_order_only_matches_sheet3(self):
        sticky = self._order(has_address=False)
        self.assertIn(sticky, Order.objects.filter(sheet_q("sheet3")))
        self.assertNotIn(sticky, Order.objects.filter(sheet_q("sheet1")))
        self.assertNotIn(sticky, Order.objects.filter(sheet_q("sheet2")))

    def test_sheet_q_complete_order_with_ingest_address_is_sheet1(self):
        normal = self._order()
        self.assertIn(normal, Order.objects.filter(sheet_q("sheet1")))
        self.assertNotIn(normal, Order.objects.filter(sheet_q("sheet3")))

    def test_sheet_q_missing_phone_still_sheet3_when_ingest_had_address(self):
        no_phone = self._order(phone="")
        self.assertIn(no_phone, Order.objects.filter(sheet_q("sheet3")))
        self.assertNotIn(no_phone, Order.objects.filter(sheet_q("sheet1")))

    def test_three_sheets_are_disjoint(self):
        self._order(has_address=False)
        self._order()
        self._order(order_source="messenger")
        self._order(phone="")
        ids1 = set(Order.objects.filter(sheet_q("sheet1")).values_list("id", flat=True))
        ids2 = set(Order.objects.filter(sheet_q("sheet2")).values_list("id", flat=True))
        ids3 = set(Order.objects.filter(sheet_q("sheet3")).values_list("id", flat=True))
        self.assertEqual(ids1 & ids2, set())
        self.assertEqual(ids1 & ids3, set())
        self.assertEqual(ids2 & ids3, set())
        self.assertEqual(len(ids1 | ids2 | ids3), 4)
