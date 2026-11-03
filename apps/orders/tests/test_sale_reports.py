"""Báo cáo sale / digital — gộp từ 3 file test theo domain (tái cấu trúc
2026-08-05, xem docs/test-restructure-2026-08-05.md):
test_digital_and_sale_staff_reports, test_sale_source_closed_revenue,
test_data_by_carrier_report. Việc gộp gỡ luôn import chéo
``_ReportFixtureMixin`` giữa các file test — mixin nay sống tại đây.
Docstring gốc của từng file giữ nguyên làm docstring class tương ứng.
"""
import pytest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.phone_carrier import CARRIER_LABELS, carrier_for_prefix
from apps.orders.reporting.shared import LEAD_TIME_CAP
from apps.orders.repositories import OrderRepository
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import make_admin


# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
pytestmark = pytest.mark.usefixtures("isolated_cache")


class _ReportFixtureMixin:
    """Số liệu nền cho 2 tab báo cáo mới (theo digital / theo sale).

    - Báo cáo theo digital lấy "số data" từ ``daily_breakdown[].leads`` (và
      ``channels[].leads``): MỌI lead trong kỳ, không chỉ đơn đã chốt.
    - Báo cáo theo sale lấy từ endpoint mới ``/api/orders/reports/sale-by-staff``:
      3 mốc doanh thu tách theo trạng thái + số data / số đơn theo từng ngày.
    """

    @classmethod
    def setUpTestData(cls):
        # Số liệu nền bất biến dựng MỘT lần cho cả class (Django rollback từng test
        # về savepoint của class) — trước đây tạo lại admin/ads/sale/kênh mỗi test.
        super().setUpTestData()
        tag = cls.__name__.lower()
        cls.admin = make_admin(f"admin-{tag}")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sale"})
        cls.ads = User.objects.create_user(username=f"ads-{tag}", password="x")
        User.objects.filter(pk=cls.ads.pk).update(department_id=DepartmentID.ADS)
        cls.sale = User.objects.create_user(username=f"sale-{tag}", password="x")
        User.objects.filter(pk=cls.sale.pk).update(department_id=DepartmentID.SALES)
        cls.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000009", phone_normalized="0900000009"
        )
        cls.channel = Channel.objects.create(code="fb-rep", name="FB Rep")
        UserChannel.objects.create(user=cls.ads, channel=cls.channel, is_active=True)

    def setUp(self):
        self.client = APIClient()

    def _order(
        self,
        total,
        sales_status_code=None,
        *,
        staff=None,
        status=0,
        order_source="landing",
        landing_confirmed="0",
        raw_address="",
        has_address=True,
    ):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=staff or self.sale,
            name="X",
            phone="0900000009",
            raw_address=raw_address,
            has_address=has_address,
            status=status,
            order_source=order_source,
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal(landing_confirmed),
        )
        if sales_status_code is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
        return order


class AdsReportLeadsTests(_ReportFixtureMixin, TestCase):
    """``leads`` = số data: mọi lead trong kỳ, kể cả lead chưa/không chốt."""

    def _payload(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_leads_counts_every_status_not_only_closed(self):
        self._order("100000", "closed")
        self._order("80000", "not_buying")
        self._order("50000", None)  # lead chưa được đánh giá

        days = self._payload()["daily_breakdown"]
        self.assertEqual(sum(day["leads"] for day in days), 3)
        self.assertEqual(sum(day["closed_orders"] for day in days), 1)

    def test_channel_level_leads_match_day_level(self):
        self._order("100000", "closed")
        self._order("80000", "no_answer_1")

        for day in self._payload()["daily_breakdown"]:
            self.assertEqual(sum(ch["leads"] for ch in day["channels"]), day["leads"])

    def test_channel_with_only_unclosed_leads_still_listed(self):
        # Trước đây channels[] chỉ dựng từ đơn ĐÃ CHỐT nên kênh chỉ có lead treo
        # biến mất hẳn -> tab digital sẽ mất hàng dù có chi phí và có data.
        self._order("80000", "not_buying")

        days = self._payload()["daily_breakdown"]
        codes = {ch["channel__code"] for day in days for ch in day["channels"]}
        self.assertEqual(codes, {"fb-rep"})
        self.assertEqual(sum(day["leads"] for day in days), 1)
        self.assertEqual(sum(day["closed_orders"] for day in days), 0)

    def test_cancelled_orders_excluded_from_leads(self):
        self._order("100000", "closed")
        self._order("90000", "closed", status=6)  # đơn huỷ

        days = self._payload()["daily_breakdown"]
        self.assertEqual(sum(day["leads"] for day in days), 1)

    def test_leads_never_below_closed_orders(self):
        self._order("100000", "closed")
        self._order("100000", "closed_scheduled_date")
        self._order("100000", "not_buying")

        for day in self._payload()["daily_breakdown"]:
            self.assertGreaterEqual(day["leads"], day["closed_orders"])
            for channel in day["channels"]:
                self.assertGreaterEqual(channel["leads"], channel["closed_orders"])


class SaleStaffReportTests(_ReportFixtureMixin, TestCase):
    URL = "/api/orders/reports/sale-by-staff"

    def _rows(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        return response.json()["by_staff_daily"]

    def test_confirmed_is_landing_snapshot_closed_is_upsell(self):
        # khách đăng ký 1tr5, sale chốt 4tr2 -> xác nhận 1tr5, chốt 2tr7
        self._order("4200000", "closed", landing_confirmed="1500000")
        # đơn hẹn ngày cùng tập 4 mã -> vẫn tính
        self._order("200000", "closed_scheduled_date", landing_confirmed="200000")
        # chưa chốt -> KHÔNG vào cột tiền nào, chỉ vào data_count
        self._order("300000", "not_buying", landing_confirmed="300000")

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(Decimal(row["total_revenue"]), Decimal("4400000"))
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("1700000"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("2700000"))
        self.assertEqual(row["data_count"], 3)
        # số đơn dùng cùng tập 4 mã với 2 cột tiền -> tỉ lệ chốt không lệch tiêu chí
        self.assertEqual(row["orders"], 2)
        self.assertEqual(row["staff_id"], self.sale.id)

    def test_order_without_snapshot_counts_fully_as_closed(self):
        # đơn tạo tay/fanpage: xác nhận 0, toàn bộ vào chốt
        self._order("3000000", "closed", landing_confirmed="0")

        row = self._rows()[0]
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("0"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("3000000"))

    def test_downsell_clamps_confirmed_no_negative_closed(self):
        # khách đăng ký 3tr, sale chốt xuống 1tr5 -> xác nhận cắt về 1tr5, chốt 0
        self._order("1500000", "closed", landing_confirmed="3000000")

        row = self._rows()[0]
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("1500000"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("0"))

    def test_confirmed_plus_closed_equals_closed_orders_revenue(self):
        self._order("4200000", "closed", landing_confirmed="1500000")
        self._order("3000000", "closed_express", landing_confirmed="0")
        self._order("1500000", "created_order", landing_confirmed="3000000")
        self._order("999000", "not_buying", landing_confirmed="999000")

        row = self._rows()[0]
        self.assertEqual(
            Decimal(row["confirmed_revenue"]) + Decimal(row["closed_revenue"]),
            Decimal("8700000"),
        )
        # Doanh thu tổng cùng tập đơn chốt -> luôn = xác nhận + chốt,
        # đơn not_buying 999k không được cộng vào.
        self.assertEqual(Decimal(row["total_revenue"]), Decimal("8700000"))

    def test_total_revenue_zero_when_no_closed_orders(self):
        # chỉ có data chưa chốt: 3 cột tiền đều 0, data_count vẫn đếm
        self._order("500000", "not_buying")
        self._order("700000", None)  # chưa được đánh giá

        row = self._rows()[0]
        self.assertEqual(Decimal(row["total_revenue"]), Decimal("0"))
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("0"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("0"))
        self.assertEqual(row["data_count"], 2)
        self.assertEqual(row["orders"], 0)

    def test_rows_split_per_day(self):
        today = self._order("100000", "closed")
        older = self._order("50000", "closed")
        Order.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )

        rows = self._rows()
        self.assertEqual(len(rows), 2)
        dates = [row["date"] for row in rows]
        self.assertEqual(dates, sorted(dates))  # sắp xếp tăng dần theo ngày
        self.assertEqual(
            sum(Decimal(row["total_revenue"]) for row in rows), Decimal("150000")
        )
        self.assertEqual(
            rows[-1]["date"], timezone.localdate(today.created_at).isoformat()
        )

    def test_requires_dashboard_section(self):
        outsider = User.objects.create_user(username="outsider-sale-rep", password="x")
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class SaleStaffLeadTimeTests(_ReportFixtureMixin, TestCase):
    """Lead time xử lý data — từ lúc data ĐẾN TAY SALE tới lần ĐẦU nhập kết quả.

    Đơn không có event bàn giao nào thì rơi về ``orders.created_at``, nên phần
    lớn test dưới đây (không dựng event bàn giao) vẫn đo từ lúc đơn đổ về.

    Backend trả tổng giây + số đơn có mốc (không trả sẵn trung bình) để FE cộng
    ngang qua ngày và qua nhân viên mà không lệch; mỗi đơn bị cắt trần
    ``LEAD_TIME_CAP`` để một data bỏ quên vài tuần không kéo lệch cả kỳ.
    """

    URL = "/api/orders/reports/sale-by-staff"

    def _rows(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        return response.json()["by_staff_daily"]

    def _lead_totals(self):
        rows = self._rows()
        return (
            sum(row["lead_time_seconds"] for row in rows),
            sum(row["lead_time_orders"] for row in rows),
        )

    def _wait_total(self):
        return sum(row["queue_wait_seconds"] for row in self._rows())

    def _handover(self, order, after, *, event_type="staff_assigned"):
        """Ghi mốc data đến tay sale, cách lúc đơn đổ về ``after``."""
        return self._touch(order, after, event_type=event_type, to_code="1")

    def _touch(self, order, after, *, event_type="sales_status_changed", to_code="closed"):
        """Ghi một mốc vòng đời cách lúc tạo đơn ``after``.

        ``created_at`` là auto_now_add nên phải UPDATE lại sau khi tạo.
        """
        event = OrderJourneyEvent.objects.create(
            order=order, event_type=event_type, to_code=to_code
        )
        OrderJourneyEvent.objects.filter(pk=event.pk).update(
            created_at=order.created_at + after
        )
        return event

    def test_lead_time_takes_first_status_change_only(self):
        order = self._order("100000", "closed")
        self._touch(order, timedelta(hours=2), to_code="no_answer_1")
        self._touch(order, timedelta(hours=5), to_code="scheduled")
        self._touch(order, timedelta(hours=30), to_code="closed")

        self.assertEqual(self._lead_totals(), (2 * 3600, 1))

    def test_untouched_order_excluded_instead_of_counted_as_cap(self):
        # Bẫy Postgres: LEAST(NULL, interval) trả về interval chứ không NULL —
        # đơn chưa ai đụng tới mà lọt vào Sum sẽ bị cộng nguyên mức trần.
        touched = self._order("100000", "closed")
        self._touch(touched, timedelta(hours=1))
        self._order("100000", None)  # data chưa được xử lý

        self.assertEqual(self._lead_totals(), (3600, 1))

    def test_lead_time_capped_per_order(self):
        forgotten = self._order("100000", "closed")
        self._touch(forgotten, timedelta(days=30))

        seconds, orders = self._lead_totals()
        self.assertEqual(seconds, int(LEAD_TIME_CAP.total_seconds()))
        self.assertEqual(orders, 1)

    def test_other_event_types_are_not_a_touch(self):
        order = self._order("100000", "closed")
        self._touch(order, timedelta(hours=1), event_type="assessment_note_updated")
        self._touch(order, timedelta(hours=4), event_type="delivery_business_status_changed")

        self.assertEqual(self._lead_totals(), (0, 0))

    def test_auto_flip_from_kiotviet_counts_as_a_touch(self):
        # Đơn bán trực tiếp: sale không bấm tay, KiotViet lật sang "đã lên đơn"
        # — vẫn là mốc data có kết quả nên phải vào chỉ số (chốt 2026-08-24).
        order = self._order("100000", "created_order")
        self._touch(order, timedelta(minutes=45), to_code="created_order")

        self.assertEqual(self._lead_totals(), (45 * 60, 1))

    def test_orders_with_different_touch_times_stay_on_one_row(self):
        # Subquery mốc tác nghiệp được annotate TRƯỚC .values(); nếu nó lọt vào
        # GROUP BY thì mỗi đơn thành một hàng riêng và mọi chỉ số của bảng (data
        # nhận, tỉ lệ chốt) vỡ theo — không chỉ cột lead time.
        early = self._order("100000", "closed")
        self._touch(early, timedelta(hours=1))
        late = self._order("100000", "closed")
        self._touch(late, timedelta(hours=5))

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data_count"], 2)
        self.assertEqual(rows[0]["lead_time_orders"], 2)
        self.assertEqual(rows[0]["lead_time_seconds"], 6 * 3600)

    def test_starts_at_handover_not_order_creation(self):
        # Data về ngoài giờ làm việc: LeadAssignmentService bỏ qua, sweep chia
        # lúc 8h sáng. Đo từ created_at ra 3h10p — đó là hàng đợi, không phải
        # thời gian sale xử lý.
        order = self._order("100000", "closed")
        self._handover(order, timedelta(hours=3))
        self._touch(order, timedelta(hours=3, minutes=10))

        self.assertEqual(self._lead_totals(), (10 * 60, 1))

    def test_ingest_designated_sale_is_a_handover(self):
        order = self._order("100000", "closed")
        self._handover(order, timedelta(minutes=1), event_type="staff_ingest_designated")
        self._touch(order, timedelta(minutes=21))

        self.assertEqual(self._lead_totals(), (20 * 60, 1))

    def test_falls_back_to_creation_when_never_handed_over(self):
        # Đơn tạo tay đã có sẵn sale, hoặc đơn cũ có trước khi hệ thống ghi event
        # chia — không có mốc nào tốt hơn lúc đơn đổ về.
        order = self._order("100000", "closed")
        self._touch(order, timedelta(hours=2))

        self.assertEqual(self._lead_totals(), (2 * 3600, 1))

    def test_reassignment_after_the_touch_does_not_reset_lead_time(self):
        # Chỉ lấy lần bàn giao còn hiệu lực LÚC tác nghiệp. Nếu lấy lần gần nhất
        # bất kể thời điểm, đơn chuyển tay sau đó sẽ cho hiệu số âm -> kẹp về 0
        # và làm đẹp số một cách giả tạo.
        order = self._order("100000", "closed")
        self._handover(order, timedelta(hours=1))
        self._touch(order, timedelta(hours=2))
        self._handover(order, timedelta(hours=5), event_type="staff_changed")

        self.assertEqual(self._lead_totals(), (3600, 1))

    def test_reassignment_before_the_touch_measures_from_the_new_owner(self):
        # Sale A ôm 4h không đụng, chuyển sang B, B xử lý sau 15p -> tính 15p cho
        # hàng của B, không bắt B gánh 4h của A.
        order = self._order("100000", "closed")
        self._handover(order, timedelta(minutes=5))
        self._handover(order, timedelta(hours=4), event_type="staff_changed")
        self._touch(order, timedelta(hours=4, minutes=15))

        self.assertEqual(self._lead_totals(), (15 * 60, 1))

    def test_queue_wait_measures_time_before_handover(self):
        order = self._order("100000", "closed")
        self._handover(order, timedelta(hours=3))
        self._touch(order, timedelta(hours=3, minutes=10))

        self.assertEqual(self._wait_total(), 3 * 3600)
        self.assertEqual(self._lead_totals(), (10 * 60, 1))

    def test_queue_wait_is_zero_when_never_handed_over(self):
        # Đơn tạo tay đã có sẵn sale: không có chặng chờ chia, toàn bộ thời gian
        # thuộc về chặng xử lý.
        order = self._order("100000", "closed")
        self._touch(order, timedelta(hours=2))

        self.assertEqual(self._wait_total(), 0)
        self.assertEqual(self._lead_totals(), (2 * 3600, 1))

    def test_two_columns_add_up_to_elapsed_time_since_order_arrived(self):
        # Bất biến của bảng: chờ chia + xử lý = từ lúc đơn đổ về tới lúc tác nghiệp.
        order = self._order("100000", "closed")
        self._handover(order, timedelta(hours=9))
        self._touch(order, timedelta(hours=9, minutes=25))
        other = self._order("100000", "closed")
        self._handover(other, timedelta(minutes=2))
        self._touch(other, timedelta(minutes=32))

        wait = self._wait_total()
        lead, orders = self._lead_totals()
        self.assertEqual(orders, 2)
        self.assertEqual(wait + lead, (9 * 3600 + 25 * 60) + 32 * 60)

    def test_queue_wait_shares_the_denominator_with_lead_time(self):
        # Đơn chưa xử lý không vào cột nào — nếu tách mẫu số, hai cột hết cộng được.
        order = self._order("100000", "closed")
        self._handover(order, timedelta(hours=1))
        self._touch(order, timedelta(hours=2))
        untouched = self._order("100000", None)
        self._handover(untouched, timedelta(hours=6))

        self.assertEqual(self._wait_total(), 3600)
        self.assertEqual(self._lead_totals(), (3600, 1))

    def test_report_stays_a_single_query(self):
        # Hai subquery tương quan (mốc tác nghiệp + mốc bàn giao) phải nằm TRONG
        # câu group-by, không được biến thành vòng lặp mỗi đơn một query.
        for _ in range(5):
            order = self._order("100000", "closed")
            self._handover(order, timedelta(minutes=5))
            self._touch(order, timedelta(minutes=20))

        # Truyền sẵn scope kênh để loại query phụ của sale_report_channel_ids()
        # — thứ cần khoá ở đây là bản thân câu báo cáo.
        with self.assertNumQueries(1):
            OrderRepository.summary_sale_daily_by_staff(sale_channel_ids=[self.channel.id])

    def test_totals_split_per_day_and_stay_additive(self):
        today = self._order("100000", "closed")
        self._touch(today, timedelta(hours=3))
        older = self._order("100000", "closed")
        Order.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=3))
        older.refresh_from_db()
        self._touch(older, timedelta(hours=1))

        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self._lead_totals(), (4 * 3600, 2))


class SaleSheetClosedRevenueTests(_ReportFixtureMixin, TestCase):
    """Nhóm 10 Báo cáo Sale — đơn chốt, đơn hoàn & doanh thu theo (sale, sheet).

    Từ 2026-08-11 trục cột là 3 tab của trang Landing (Landing xác nhận đơn /
    Messenger xác nhận đơn / Tư vấn) thay cho nguồn đơn, dùng đúng
    ``sheet_rules.sheet_q`` mà màn hình dùng nên báo cáo không lệch tab.

    Vẫn khác Nhóm 9 ở hai điểm: chỉ nhìn 4 nguồn lead nội bộ (bỏ sàn / KOC /
    KOL / đơn chưa gắn nguồn) và tách thêm số đơn hoàn (rổ 4/5/12/13) + doanh
    thu giao thành công (chỉ đơn ``status=3``, từ 2026-09-01 — trước đó là
    "tổng trừ tiền đơn hoàn" nên còn tính cả đơn chưa giao xong).
    """

    URL = "/api/orders/dashboard/summary"

    def _rows(self):
        return OrderRepository.summary_closed_revenue_by_staff_sheet()

    def _row_for(self, rows, sheet):
        matches = [row for row in rows if row["sheet"] == sheet]
        self.assertEqual(len(matches), 1, f"kỳ vọng đúng 1 hàng cho sheet {sheet}")
        return matches[0]

    def test_returned_orders_counted_and_deducted_from_net_revenue(self):
        self._order("100000", "closed", status=3)  # giao thành công
        self._order("200000", "closed_express", status=5)  # đã hoàn
        self._order("400000", "created_order", status=12)  # chờ lấy hàng hoàn

        row = self._row_for(self._rows(), "sheet3")
        self.assertEqual(row["closed_orders"], 3)
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("700000"))
        self.assertEqual(row["returned_orders"], 2)
        # doanh thu giao thành công = tiền của đúng đơn đã giao xong
        self.assertEqual(Decimal(row["net_revenue"]), Decimal("100000"))

    def test_in_delivery_orders_not_counted_as_delivered_revenue(self):
        # Đơn đã chốt nhưng còn trong kho / đang đi đường / chờ giao lại CHƯA
        # phải doanh thu giao thành công. Trước 2026-09-01 cột này chỉ trừ đơn
        # hoàn nên ở các kỳ gần đây (phần lớn đơn chưa có kết quả giao) nó phồng
        # gấp nhiều lần số thật.
        self._order("100000", "closed", status=3)  # giao thành công
        self._order("200000", "closed", status=2)  # đang giao
        self._order("400000", "closed", status=0)  # chưa xuất kho
        self._order("800000", "closed", status=10)  # chờ giao lại

        row = self._row_for(self._rows(), "sheet3")
        self.assertEqual(row["closed_orders"], 4)
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("1500000"))
        self.assertEqual(row["returned_orders"], 0)
        self.assertEqual(Decimal(row["net_revenue"]), Decimal("100000"))

    def test_whole_return_bucket_counted_as_returned(self):
        # 4 và 13 cùng rổ hoàn với 5/12 — không chỉ đơn đã hoàn xong mới tính.
        self._order("100000", "closed", status=4)
        self._order("200000", "closed", status=13)

        row = self._row_for(self._rows(), "sheet3")
        self.assertEqual(row["closed_orders"], 2)
        self.assertEqual(row["returned_orders"], 2)
        self.assertEqual(Decimal(row["net_revenue"]), Decimal("0"))

    def test_scheduled_and_unclosed_statuses_not_counted(self):
        self._order("100000", "closed", status=3)
        self._order("200000", "closed_scheduled_date")  # "đã chốt, hẹn ngày"
        self._order("400000", "need_more_consulting")
        self._order("800000", None)  # lead chưa đánh giá

        row = self._row_for(self._rows(), "sheet3")
        self.assertEqual(row["closed_orders"], 1)
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("100000"))
        self.assertEqual(Decimal(row["net_revenue"]), Decimal("100000"))

    def test_cancelled_orders_excluded(self):
        self._order("100000", "closed")
        self._order("900000", "closed", status=6)

        row = self._row_for(self._rows(), "sheet3")
        self.assertEqual(row["closed_orders"], 1)
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("100000"))

    def test_rows_split_per_sheet(self):
        # Đủ Tên+SĐT+Địa chỉ và ingest có địa chỉ → Sheet 1 / Sheet 2 theo nguồn.
        self._order("100000", "closed", raw_address="1 Nguyen Trai")
        self._order("200000", "closed", order_source="messenger", raw_address="2 Le Loi")
        # Thiếu địa chỉ → Tư vấn.
        self._order("400000", "closed")

        rows = self._rows()
        self.assertEqual({row["sheet"] for row in rows}, {"sheet1", "sheet2", "sheet3"})
        self.assertEqual(
            Decimal(self._row_for(rows, "sheet2")["closed_revenue"]), Decimal("200000")
        )
        self.assertEqual(
            sum(Decimal(row["closed_revenue"]) for row in rows), Decimal("700000")
        )

    def test_sticky_consulting_order_counted_in_sheet3_not_sheet1(self):
        # Đơn đổ về không địa chỉ, sale điền đủ rồi chốt → vẫn là doanh thu Tư vấn.
        self._order("500000", "closed", raw_address="3 Tran Hung Dao", has_address=False)

        rows = self._rows()
        self.assertEqual([row["sheet"] for row in rows], ["sheet3"])
        self.assertEqual(Decimal(rows[0]["closed_revenue"]), Decimal("500000"))

    def test_marketplace_koc_and_sourceless_orders_excluded(self):
        self._order("100000", "closed")
        self._order("200000", "closed", order_source="tiktok")
        self._order("400000", "closed", order_source="shopee")
        self._order("800000", "closed", order_source="koc")
        # "kol" là tên cũ của "koc" — loại cùng nhóm, không được lọt vào bảng.
        self._order("1600000", "closed", order_source="kol")
        self._order("3200000", "closed", order_source=None)

        rows = self._rows()
        self.assertEqual([row["sheet"] for row in rows], ["sheet3"])
        self.assertEqual(Decimal(rows[0]["closed_revenue"]), Decimal("100000"))

    def test_rows_split_per_staff(self):
        other = User.objects.create_user(username="sale-2-src", password="x")
        User.objects.filter(pk=other.pk).update(department_id=DepartmentID.SALES)
        self._order("100000", "closed")
        self._order("200000", "closed", staff=other)

        rows = self._rows()
        self.assertEqual({row["staff_id"] for row in rows}, {self.sale.id, other.id})

    def test_revenue_fields_are_strings(self):
        self._order("100000", "closed")

        row = self._rows()[0]
        self.assertIsInstance(row["closed_revenue"], str)
        self.assertIsInstance(row["net_revenue"], str)

    def test_exposed_on_dashboard_summary(self):
        self._order("100000", "closed", status=5)
        self.client.force_authenticate(user=self.admin)

        payload = self.client.get(self.URL).json()
        rows = payload["closed_revenue_by_staff_sheet"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sheet"], "sheet3")
        self.assertEqual(rows[0]["closed_orders"], 1)
        self.assertEqual(rows[0]["returned_orders"], 1)

    def test_requires_dashboard_section(self):
        outsider = User.objects.create_user(username="outsider-sale-source", password="x")
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class PhoneCarrierMappingTests(SimpleTestCase):
    """Block "Data theo nhà mạng" trong báo cáo sale (dashboard summary).

    Khóa hành vi:
    - Nhận diện nhà mạng theo 3 số đầu SAU normalize (bỏ ký tự lạ, ``84…``→``0…``,
      thêm lại số 0 đầu bị mất kiểu Excel).
    - Cùng scope với các block sale khác: chỉ kênh có NV Ads gán + staff phòng Sales.
    - Zero-fill: nhà mạng 0 data vẫn có dòng; số không nhận diện được vào "unknown".
    """

    def test_prefix_mapping_matches_expected_carriers(self):
        self.assertEqual(carrier_for_prefix("098"), "viettel")
        self.assertEqual(carrier_for_prefix("034"), "viettel")
        self.assertEqual(carrier_for_prefix("091"), "vinaphone")
        self.assertEqual(carrier_for_prefix("083"), "vinaphone")
        self.assertEqual(carrier_for_prefix("090"), "mobifone")
        self.assertEqual(carrier_for_prefix("077"), "mobifone")
        self.assertEqual(carrier_for_prefix("092"), "vietnamobile")
        self.assertEqual(carrier_for_prefix("099"), "gmobile")
        self.assertEqual(carrier_for_prefix("087"), "itel")
        self.assertEqual(carrier_for_prefix("055"), "wintel")
        # Số bàn Hà Nội / prefix lạ -> unknown
        self.assertEqual(carrier_for_prefix("024"), "unknown")
        self.assertEqual(carrier_for_prefix(""), "unknown")


class DataByCarrierReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Dữ liệu bất biến dựng MỘT lần cho cả class.
        cls.admin = make_admin("admin-carrier")
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})

        cls.sale = User.objects.create_user(username="sale-carrier", password="x")
        User.objects.filter(pk=cls.sale.pk).update(department_id=DepartmentID.SALES)
        ads = User.objects.create_user(username="ads-carrier", password="x")
        User.objects.filter(pk=ads.pk).update(department_id=DepartmentID.ADS)

        cls.channel = Channel.objects.create(code="lp-carrier", name="Landing Carrier")
        UserChannel.objects.create(user=ads, channel=cls.channel, is_active=True)
        # Kênh không gán NV Ads -> ngoài phạm vi báo cáo sale
        cls.channel_noads = Channel.objects.create(code="lp-carrier-noads", name="No Ads")

        cls.customer = Customer.objects.create(
            full_name="KH Carrier", phone_raw="0987000111", phone_normalized="0987000111"
        )

        # Viettel: dạng chuẩn + dạng +84 kèm ký tự lạ
        cls._order("0987654321")
        cls._order("+84 98 765-4322")
        # Vinaphone: 9 số mất số 0 đầu (Excel)
        cls._order("912345678")
        # MobiFone
        cls._order("0705555555")
        # Không nhận diện được: số bàn + rỗng
        cls._order("0241234567")
        cls._order("")
        # Đơn trên kênh không gán ads -> phải bị loại khỏi thống kê
        cls._order("0987654399", channel=cls.channel_noads)

    def setUp(self):
        self.client = APIClient()

    @classmethod
    def _order(cls, phone, *, channel=None):
        return Order.objects.create(
            channel=channel or cls.channel,
            customer=cls.customer,
            staff=cls.sale,
            name="X",
            phone=phone,
            status=0,
            order_source="landing",
        )

    def _summary(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_counts_by_carrier_with_normalized_prefixes(self):
        rows = {r["carrier"]: r for r in self._summary()["data_by_carrier"]}

        self.assertEqual(rows["viettel"]["total"], 2)
        self.assertEqual(rows["vinaphone"]["total"], 1)
        self.assertEqual(rows["mobifone"]["total"], 1)
        self.assertEqual(rows["unknown"]["total"], 2)
        self.assertEqual(rows["viettel"]["label"], CARRIER_LABELS["viettel"])
        self.assertEqual(rows["unknown"]["label"], "Không xác định")

    def test_zero_fill_lists_all_known_carriers(self):
        rows = {r["carrier"] for r in self._summary()["data_by_carrier"]}
        self.assertEqual(
            rows,
            {
                "viettel",
                "vinaphone",
                "mobifone",
                "vietnamobile",
                "gmobile",
                "itel",
                "wintel",
                "unknown",
            },
        )

    def test_sorted_by_total_desc(self):
        totals = [r["total"] for r in self._summary()["data_by_carrier"]]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_respects_date_filter(self):
        Order.objects.filter(channel=self.channel).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        date_from = (timezone.now() - timedelta(days=3)).date().isoformat()
        payload = self._summary(date_from=date_from)
        totals = {r["carrier"]: r["total"] for r in payload["data_by_carrier"]}
        self.assertTrue(all(total == 0 for total in totals.values()))


class SaleResaleByStaffTests(_ReportFixtureMixin, TestCase):
    """Tab Resale — đơn của KÊNH Resale, chia 4 rổ như Báo cáo tổng.

    Xem docs/superpowers/specs/2026-08-26-resale-channel-report-design.md.
    """

    def setUp(self):
        super().setUp()
        self.resale_channel = Channel.objects.create(code="resale-test", name="Resale Test")
        self.other_channel = Channel.objects.create(code="other-test", name="Other Test")
        # BẮT BUỘC: conftest có fixture autouse ép SALE_FB_CHANNEL_IDS =
        # (9_999_000_000,) cho cả suite. Không patch lại thì mọi rổ ra 0 và test
        # "xanh" vì lý do hoàn toàn sai.
        patcher = mock.patch(
            "apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS",
            (self.resale_channel.id,),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _order(self, total, *, status=3, channel=None, staff=None, invoice_status=1):
        return Order.objects.create(
            channel=channel or self.resale_channel,
            customer=self.customer,
            staff=staff or self.sale,
            name="X",
            phone="0900000009",
            has_address=True,
            status=status,
            invoice_status=invoice_status,
            order_source="landing",
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal("0"),
        )

    def _kpis(self):
        return OrderRepository.summary_resale_by_staff()["kpis"]

    def test_order_outside_resale_channel_is_ignored(self):
        self._order("100000", channel=self.other_channel)

        kpis = self._kpis()
        self.assertEqual(kpis["total_orders"], 0)
        self.assertEqual(kpis["total_revenue"], "0")

    def test_status_lands_in_its_bucket(self):
        """4 rổ theo status: giao thành công / đang giao / đã hoàn / nguy cơ hoàn."""
        # Mỗi rổ có key riêng nên các vòng cộng dồn không đè nhau; `return_risk`
        # đứng trước `returned` để còn khoá được "nguy cơ hoàn KHÔNG tính là đã hoàn".
        for status, total, bucket, extra in (
            (3, "100000", "delivered", {"total_revenue": "100000.00"}),
            (2, "80000", "awaiting", {}),
            (10, "50000", "return_risk", {"returned_orders": 0}),
            (4, "60000", "returned", {}),
        ):
            with self.subTest(status=status, bucket=bucket):
                self._order(total, status=status)
                kpis = self._kpis()
                self.assertEqual(kpis[f"{bucket}_orders"], 1)
                self.assertEqual(kpis[f"{bucket}_revenue"], f"{total}.00")
                for key, expected in extra.items():
                    self.assertEqual(kpis[key], expected)

    def test_unknown_status_falls_into_awaiting_not_out_of_every_bucket(self):
        # Rổ "chờ giao" là PHÉP TRỪ, nên mã lạ (KiotViet thêm mới) phải rơi vào
        # đây thay vì biến mất khỏi cả 4 rổ và làm tổng lệch.
        self._order("70000", status=99)

        kpis = self._kpis()
        self.assertEqual(kpis["awaiting_orders"], 1)
        self.assertEqual(kpis["total_orders"], 1)

    def test_cancelled_orders_are_in_no_bucket(self):
        self._order("100000", status=6)
        self._order("90000", status=3, invoice_status=2)

        self.assertEqual(self._kpis()["total_orders"], 0)

    def test_lead_order_without_invoice_is_in_no_bucket(self):
        self._order("100000", status=3, invoice_status=None)

        self.assertEqual(self._kpis()["total_orders"], 0)

    def test_four_buckets_always_sum_to_total(self):
        for status, total in ((3, "100000"), (2, "80000"), (4, "60000"), (10, "50000"), (99, "40000")):
            self._order(total, status=status)

        kpis = self._kpis()
        self.assertEqual(
            kpis["delivered_orders"] + kpis["awaiting_orders"]
            + kpis["returned_orders"] + kpis["return_risk_orders"],
            kpis["total_orders"],
        )
        self.assertEqual(
            Decimal(kpis["delivered_revenue"]) + Decimal(kpis["awaiting_revenue"])
            + Decimal(kpis["returned_revenue"]) + Decimal(kpis["return_risk_revenue"]),
            Decimal(kpis["total_revenue"]),
        )

    def test_order_with_live_restore_clone_is_excluded_once(self):
        original = self._order("100000", status=3)
        clone = self._order("100000", status=3)
        Order.objects.filter(pk=clone.pk).update(restored_from_id=original.pk)

        # Đơn gốc bị loại (đã có bản khôi phục còn sống); chỉ clone được tính.
        kpis = self._kpis()
        self.assertEqual(kpis["total_orders"], 1)
        self.assertEqual(kpis["total_revenue"], "100000.00")

    def test_order_without_staff_still_counted_and_gets_its_own_row(self):
        order = self._order("100000", status=3)
        Order.objects.filter(pk=order.pk).update(staff=None)

        payload = OrderRepository.summary_resale_by_staff()
        self.assertEqual(payload["kpis"]["total_orders"], 1)
        self.assertEqual([row["staff_id"] for row in payload["by_staff"]], [None])

    def test_kpis_equal_the_sum_of_by_staff_rows(self):
        other = User.objects.create_user(username=f"sale2-{id(self)}", password="x")
        User.objects.filter(pk=other.pk).update(department_id=DepartmentID.SALES)
        self._order("100000", status=3)
        self._order("80000", status=2, staff=other)
        self._order("60000", status=4, staff=other)

        payload = OrderRepository.summary_resale_by_staff()
        for field in ("total_orders", "delivered_orders", "awaiting_orders", "returned_orders"):
            self.assertEqual(
                sum(row[field] for row in payload["by_staff"]),
                payload["kpis"][field],
                f"cot {field} khong khop giua kpis va by_staff",
            )
        self.assertEqual(
            sum(Decimal(row["total_revenue"]) for row in payload["by_staff"]),
            Decimal(payload["kpis"]["total_revenue"]),
        )

    def test_by_staff_sorted_by_delivered_revenue_desc(self):
        other = User.objects.create_user(username=f"sale3-{id(self)}", password="x")
        User.objects.filter(pk=other.pk).update(department_id=DepartmentID.SALES)
        self._order("50000", status=3)
        self._order("90000", status=3, staff=other)

        rows = OrderRepository.summary_resale_by_staff()["by_staff"]
        self.assertEqual([row["staff_id"] for row in rows], [other.id, self.sale.id])


class SaleResaleReportApiTests(_ReportFixtureMixin, TestCase):
    URL = "/api/orders/reports/resale-by-staff"

    BUCKET_FIELDS = {
        "total_revenue", "total_orders",
        "delivered_revenue", "delivered_orders",
        "awaiting_revenue", "awaiting_orders",
        "returned_revenue", "returned_orders",
        "return_risk_revenue", "return_risk_orders",
    }

    def setUp(self):
        super().setUp()
        self.resale_channel = Channel.objects.create(code="resale-api", name="Resale API")
        patcher = mock.patch(
            "apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS",
            (self.resale_channel.id,),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL, params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _order(self, total, *, status=3):
        return Order.objects.create(
            channel=self.resale_channel, customer=self.customer, staff=self.sale,
            name="X", phone="0900000009", has_address=True, status=status,
            invoice_status=1, order_source="landing",
            total_after_discount=Decimal(total), landing_confirmed_amount=Decimal("0"),
        )

    def test_payload_has_kpis_and_by_staff_with_bucket_fields(self):
        self._order("100000", status=3)
        self._order("80000", status=2)

        body = self._payload()
        self.assertEqual(set(body), {"kpis", "by_staff"})
        self.assertEqual(set(body["kpis"]), self.BUCKET_FIELDS)
        row = body["by_staff"][0]
        self.assertEqual(
            set(row),
            self.BUCKET_FIELDS | {"staff_id", "staff__username", "staff__display_name"},
        )
        self.assertEqual(body["kpis"]["delivered_revenue"], "100000.00")
        self.assertEqual(body["kpis"]["awaiting_revenue"], "80000.00")
        self.assertEqual(body["kpis"]["total_revenue"], "180000.00")

    def test_empty_range_returns_zeroed_kpis_not_error(self):
        body = self._payload(date_from="2020-01-01", date_to="2020-01-02")
        self.assertEqual(body["by_staff"], [])
        self.assertEqual(body["kpis"]["total_revenue"], "0")
        self.assertEqual(body["kpis"]["total_orders"], 0)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))

    def test_user_without_dashboard_section_is_denied(self):
        outsider = User.objects.create_user(username=f"outsider-{id(self)}", password="x")
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
