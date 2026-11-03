"""Báo cáo tổng (GET /api/orders/reports/overview) — summary_overview.

Doanh thu tính theo TRẠNG THÁI SALE (chốt 2026-08-11): đơn vào tổng khi đã có
trạng thái sale thuộc 4 mã chốt HOẶC đã lên hóa đơn KiotViet mà chưa gắn trạng
thái sale (đơn sàn/tạo thẳng, không qua luồng CRM) — trừ đơn hủy. Tổng đó chia
đúng 4 rổ RỜI NHAU theo trạng thái giao hàng: giao thành công, chờ giao, hoàn,
rủi ro hoàn — nên 4 rổ luôn cộng lại bằng tổng, cả tiền lẫn số đơn. Khối theo
kênh bán và địa lý dùng CÙNG cơ sở tổng này (Task 3, thay cho cơ sở "net" cũ) —
nên tổng mọi khối donut/địa lý luôn khớp ô KPI tương ứng. Chi phí Ads tách làm
hai ô rời — FB (×1.1, riêng kỳ trong tháng 8/2026 ×1.08) và Google (×1.08), mọi
ad account, không giới hạn kênh gán
— và CHỈ có số khi lọc ngoại sàn (user chốt 2026-08-20): tiền FB/Google chỉ đẻ ra
đơn ngoại sàn, để chung với doanh thu Shopee/TikTok là sai mẫu số.

Gộp thêm (tái cấu trúc 2026-08-05): test_overview_report_product_filter và
test_overview_report_sale_channel — docstring gốc giữ nguyên làm docstring class.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.customers.models import Customer
from apps.facebook_ads.models import FacebookAdAccount, FacebookAdAccountDailySpend, FacebookApp
from apps.google_ads.models import GoogleAdsAccount, GoogleAdsAccountDailySpend, GoogleAdsConfig
from apps.orders.models import Order, OrderAssessment, OrderItem
from apps.catalog.models import Product
from apps.orders.repositories import OrderRepository
from apps.users.tests.helpers import make_admin, make_user_with_sections


def seed_ads_spend():
    """Chi tiêu Ads mẫu trong tháng 7/2026: FB 100 + Google 200, kèm 1 dòng ngoài kỳ."""
    app = FacebookApp.objects.create(name="BM", app_id="fb1", app_secret="s", access_token="t")
    fb_acc = FacebookAdAccount.objects.create(app=app, account_id="1", fb_id="act_1", name="A1")
    FacebookAdAccountDailySpend.objects.create(
        ad_account=fb_acc, date=date(2026, 7, 10), spend=Decimal("100")
    )
    cfg = GoogleAdsConfig.objects.create(
        name="MCC",
        developer_token="d",
        client_id="c",
        client_secret="cs",
        refresh_token="r",
        login_customer_id="1234567890",
    )
    g_acc = GoogleAdsAccount.objects.create(config=cfg, customer_id="123", name="G1")
    GoogleAdsAccountDailySpend.objects.create(
        ad_account=g_acc, date=date(2026, 7, 10), spend=Decimal("200")
    )
    # Ngoài khoảng lọc → không được tính.
    FacebookAdAccountDailySpend.objects.create(
        ad_account=fb_acc, date=date(2026, 6, 1), spend=Decimal("999")
    )


class OverviewReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview")
        self.channel = Channel.objects.create(id=920001, code="ov-ch", name="OV Channel")
        self.customer = Customer.objects.create(
            full_name="KH OV", phone_raw="0900000001", phone_normalized="0900000001"
        )

    def _order(self, total, *, status=0, invoice_status=None, source="landing", **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000001",
            status=status,
            invoice_status=invoice_status,
            order_source=source,
            total_after_discount=Decimal(total),
            **kwargs,
        )

    def _closed(self, total, *, status=0, invoice_status=3, sales_status="closed", **kwargs):
        """Đơn kèm bản đánh giá CRM — `sales_status=None` nghĩa là KHÔNG tạo đánh giá."""
        order = self._order(total, status=status, invoice_status=invoice_status, **kwargs)
        if sales_status is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order

    def _payload(self, user=None, query=""):
        self.client.force_authenticate(user=user or self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_buckets_partition_and_sources(self):
        self._closed("100000", status=0, source="landing")  # chờ giao
        self._closed("200000", status=3, source="messenger")  # giao thành công
        self._closed("50000", status=4)  # hoàn (đang chuyển hoàn)
        self._closed("30000", status=5)  # hoàn (đã hoàn)
        self._closed("70000", status=10)  # rủi ro hoàn (chờ giao lại)
        self._closed("40000", status=0, invoice_status=2)  # loại: hóa đơn hủy
        self._closed("60000", status=6)  # loại: đơn đã hủy (dù mang trạng thái sale chốt)
        self._order("500000", status=0)  # loại: lead chưa chốt trạng thái sale
        # Không tính: đơn đã xóa + chi nhánh nội bộ.
        self._order("999999", status=0, deleted_at=timezone.now())
        self._order("888888", status=0, branch_id=EXCLUDED_BRANCH_IDS[0])

        data = self._payload()
        self.assertEqual(data["total_revenue"], "450000.00")
        self.assertEqual(data["total_orders"], 5)
        self.assertEqual(data["delivered_revenue"], "200000.00")
        self.assertEqual(data["delivered_orders"], 1)
        self.assertEqual(data["awaiting_revenue"], "100000.00")
        self.assertEqual(data["awaiting_orders"], 1)
        self.assertEqual(data["returned_revenue"], "80000.00")
        self.assertEqual(data["returned_orders"], 2)
        self.assertEqual(data["return_risk_revenue"], "70000.00")
        self.assertEqual(data["return_risk_orders"], 1)
        # landing + messenger đều không phải sàn/Facebook/Web → "ngoại sàn khác".
        self.assertEqual(
            data["revenue_by_sale_channel"],
            [
                {
                    "sale_channel": "off_marketplace",
                    "revenue": "450000.00",
                    "orders": 5,
                    # Đơn messenger có status=3 → là đơn giao thành công.
                    "delivered_revenue": "200000.00",
                    "delivered_orders": 1,
                },
            ],
        )

    def test_returned_and_return_risk_by_sale_channel(self):
        self._order("100000", status=0, invoice_status=3, source="landing")  # chờ giao
        self._order("50000", status=4, invoice_status=3, source="landing")  # hoàn, ngoại sàn
        self._order("30000", status=5, invoice_status=3, source="shopee")  # hoàn, nội sàn
        self._order("40000", status=10, invoice_status=3, source="landing")  # rủi ro hoàn, ngoại sàn
        self._order("60000", status=10, invoice_status=3, source="tiktok")  # rủi ro hoàn, nội sàn

        data = self._payload()
        self.assertEqual(
            data["returned_by_sale_channel"],
            [
                # Thứ tự CỐ ĐỊNH nội sàn → ngoại sàn, không sort theo giá trị.
                {"sale_channel": "marketplace", "revenue": "30000.00", "orders": 1},
                {"sale_channel": "off_marketplace", "revenue": "50000.00", "orders": 1},
            ],
        )
        self.assertEqual(
            data["return_risk_by_sale_channel"],
            [
                {"sale_channel": "marketplace", "revenue": "60000.00", "orders": 1},
                {"sale_channel": "off_marketplace", "revenue": "40000.00", "orders": 1},
            ],
        )
        self.assertNotIn("cancelled_by_sale_channel", data)
        # Nhóm 0 đơn trong bucket không xuất hiện.
        self.assertEqual(len(data["return_risk_by_sale_channel"]), 2)

    def test_sale_channel_rows_split_buckets_and_keep_sourceless_orders(self):
        self._order("100000", status=0, invoice_status=3, source="koc")
        self._order("50000", status=5, invoice_status=3, source="koc")  # hoàn → vẫn vào TỔNG
        self._order("20000", status=0, invoice_status=3, source=None)  # không nguồn → dòng source=""

        data = self._payload()
        self.assertEqual(data["total_revenue"], "170000.00")
        # koc + đơn không nguồn đều rơi vào "ngoại sàn khác" → 1 dòng gộp.
        self.assertEqual(
            data["revenue_by_sale_channel"],
            [
                {
                    "sale_channel": "off_marketplace",
                    "revenue": "170000.00",
                    "orders": 3,
                    "delivered_revenue": "0",
                    "delivered_orders": 0,
                },
            ],
        )
        self.assertEqual(
            data["returned_by_sale_channel"],
            [{"sale_channel": "off_marketplace", "revenue": "50000.00", "orders": 1}],
        )

    def test_ads_cost_split_by_platform_with_multipliers(self):
        seed_ads_spend()

        data = self._payload(
            query="?date_from=2026-07-01&date_to=2026-07-31&sale_channel=off_marketplace"
        )
        # 100×1.1 = 110 ; 200×1.08 = 216 ; tổng 326.
        self.assertEqual(Decimal(data["ads_cost_facebook"]), Decimal("110.00"))
        self.assertEqual(Decimal(data["ads_cost_google"]), Decimal("216.00"))
        self.assertEqual(Decimal(data["ads_cost"]), Decimal("326.00"))

    def test_facebook_ads_cost_uses_the_new_multiplier_from_august_2026(self):
        """Kỳ bắt đầu trong tháng 8/2026 → chi phí FB ×1.08 thay vì ×1.1 (chốt 26/08).

        Google KHÔNG đổi: hai nền tảng có lịch đổi hệ số riêng, gộp làm một hằng số
        là lần sau đổi một bên thì bên kia lệch theo mà không ai để ý. Bảng mốc /
        kỳ bắc qua mốc / về lại 1.1 từ 09/2026: unit test
        `test_facebook_ads_cost_multiplier.py` — báo cáo chỉ giữ 1 test "có áp hệ số".
        """
        seed_ads_spend()
        app = FacebookApp.objects.get(app_id="fb1")
        FacebookAdAccountDailySpend.objects.create(
            ad_account=FacebookAdAccount.objects.get(app=app, account_id="1"),
            date=date(2026, 8, 10),
            spend=Decimal("100"),
        )

        data = self._payload(
            query="?date_from=2026-08-01&date_to=2026-08-31&sale_channel=off_marketplace"
        )

        self.assertEqual(Decimal(data["ads_cost_facebook"]), Decimal("108.00"))

    def test_ads_cost_zero_unless_filtering_off_marketplace(self):
        """Chỉ lọc ngoại sàn mới ra số — xem docstring module."""
        seed_ads_spend()
        period = "?date_from=2026-07-01&date_to=2026-07-31"

        for query, label in (
            (period, "không lọc kênh"),
            (f"{period}&sale_channel=marketplace", "lọc nội sàn"),
        ):
            with self.subTest(label):
                data = self._payload(query=query)
                self.assertEqual(Decimal(data["ads_cost_facebook"]), Decimal("0"))
                self.assertEqual(Decimal(data["ads_cost_google"]), Decimal("0"))
                self.assertEqual(Decimal(data["ads_cost"]), Decimal("0"))

    def test_date_filter_applies_to_created_at(self):
        inside = self._order("100000", status=0, invoice_status=3)
        Order.objects.filter(pk=inside.pk).update(
            created_at=timezone.make_aware(timezone.datetime(2026, 7, 10, 12, 0))
        )
        outside = self._order("999999", status=0, invoice_status=3)
        Order.objects.filter(pk=outside.pk).update(
            created_at=timezone.make_aware(timezone.datetime(2026, 6, 1, 12, 0))
        )

        data = self._payload(query="?date_from=2026-07-01&date_to=2026-07-31")
        self.assertEqual(data["total_revenue"], "100000.00")
        self.assertEqual(data["total_orders"], 1)

    def test_requires_overview_report_section(self):
        viewer = make_user_with_sections("ov-viewer", ["overview-report"])
        outsider = make_user_with_sections("ov-outsider", ["ads-report"])

        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get("/api/orders/reports/overview").status_code, 403)

        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.get("/api/orders/reports/overview").status_code, 200)

    def test_orders_without_source_stay_in_bucket_totals(self):
        """Đơn thiếu order_source vẫn phải nằm trong các khối theo nguồn.

        Bài học 2026-07-20: loại chúng làm tổng donut nhỏ hơn ô KPI mà người
        đọc không giải thích được — nặng nhất ở bucket rủi ro hoàn (hóa đơn tạo
        thẳng trên KiotViet không mang nguồn lead).
        """
        self._order("100000", status=0, invoice_status=3, source="landing")
        self._order("50000", status=0, invoice_status=3, source=None)
        self._order("30000", status=0, invoice_status=3, source="")
        self._order("40000", status=5, invoice_status=3, source=None)  # hoàn
        self._order("60000", status=10, invoice_status=3, source=None)  # rủi ro hoàn
        self._order("70000", status=10, invoice_status=3, source="")  # rủi ro hoàn

        data = self._payload()
        for total_key, rows_key in (
            ("total_revenue", "revenue_by_sale_channel"),
            ("returned_revenue", "returned_by_sale_channel"),
            ("return_risk_revenue", "return_risk_by_sale_channel"),
        ):
            self.assertEqual(
                sum(Decimal(row["revenue"]) for row in data[rows_key]),
                Decimal(data[total_key]),
                f"{rows_key} phải cộng đúng bằng {total_key}",
            )
        # Đơn thiếu order_source vẫn được tính, rơi vào nhóm "ngoại sàn khác".
        self.assertEqual(
            [(r["sale_channel"], r["revenue"]) for r in data["return_risk_by_sale_channel"]],
            [("off_marketplace", "130000.00")],
        )

    def test_revenue_by_region_and_province(self):
        self._order("100000", status=3, invoice_status=3, province="Hà Nội")
        self._order("50000", status=0, invoice_status=3, province="Hải Phòng")
        # 2 biến thể chính tả cùng tỉnh → gộp 1 dòng (Hoà Bình có thật ở prod).
        self._order("30000", status=0, invoice_status=3, province="Hoà Bình")
        self._order("20000", status=0, invoice_status=3, province="Hòa Bình")
        self._order("70000", status=0, invoice_status=3, province="Hồ Chí Minh")
        # province trống nhưng địa chỉ KiotViet có tỉnh → vẫn vào đúng tỉnh.
        self._order(
            "40000", status=0, invoice_status=3, province="",
            kiotviet_location="Hà Nội - Quận Cầu Giấy",
        )
        # Không có địa chỉ nào (đơn mới chưa nhập) → nhóm "Chưa có địa chỉ",
        # KHÔNG bị loại, để tổng khối địa lý khớp doanh thu tổng.
        self._order("10000", status=0, invoice_status=3, province="", kiotviet_location="")
        # Đơn hoàn thuộc TỔNG nhưng KHÔNG vào 2 khối địa lý: chúng ở cơ sở
        # THUẦN (user chốt 2026-08-22).
        self._order("60000", status=5, invoice_status=3, province="Hà Nội")
        # Rủi ro hoàn (mã 10) thì VẪN vào — hàng chưa quay về kho.
        self._order("25000", status=10, invoice_status=3, province="Hà Nội")
        # Đơn hủy + LEAD chưa chốt trạng thái sale KHÔNG tính vào tổng, nên cũng
        # không vào phân bổ địa lý.
        self._order("80000", status=6, invoice_status=3, province="Hà Nội")
        self._order("90000", status=0, province="Hà Nội")

        data = self._payload()
        # Tổng 2 khối địa lý phải khớp DOANH THU THUẦN = tổng trừ hoàn (bài học
        # 2026-07-20: bỏ đơn trống tỉnh làm 2 con số trên UI lệch nhau mà không
        # giải thích được — bất biến giữ nguyên, chỉ đổi vế phải).
        net = Decimal(data["total_revenue"]) - Decimal(data["returned_revenue"])
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_region"]), net)
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_province"]), net)
        # Và KHÁC tổng — nếu hai vế bằng nhau nghĩa là đơn hoàn đã lọt vào khối.
        self.assertNotEqual(net, Decimal(data["total_revenue"]))
        self.assertEqual(
            [(r["region"], r["revenue"], r["orders"]) for r in data["revenue_by_region"]],
            [
                ("northern_mountains", "50000.00", 2),
                # Hà Nội 100k + 40k (suy từ kiotviet_location) + 25k (rủi ro hoàn)
                # + Hải Phòng 50k. KHÔNG có 60k của đơn hoàn.
                ("red_river_delta", "215000.00", 4),
                ("southeast", "70000.00", 1),
                # Nhóm chưa có địa chỉ xếp cuối.
                ("unknown", "10000.00", 1),
            ],
        )
        # Tỉnh sort theo doanh thu giảm dần; Hoà/Hòa Bình đã gộp thành 1 dòng;
        # "Chưa có địa chỉ" luôn ở cuối dù giá trị bao nhiêu.
        self.assertEqual(
            [(r["province"], r["revenue"], r["orders"]) for r in data["revenue_by_province"]],
            [
                ("Hà Nội", "165000.00", 3),
                ("Hồ Chí Minh", "70000.00", 1),
                ("Hòa Bình", "50000.00", 2),
                ("Hải Phòng", "50000.00", 1),
                ("Chưa có địa chỉ", "10000.00", 1),
            ],
        )

    def test_lead_without_invoice_stays_out(self):
        """MỌI lead chưa lên hóa đơn đều ngoài tổng, kể cả lead sale đã chốt.

        Đây là bug "phồng số" 2026-07-20: 806 lead ≈ 1,28 tỷ (20% doanh thu) từng
        bị cộng vào doanh thu vì ``~Q(invoice_status=2)`` giữ lại hàng NULL.
        Từ 2026-08-20 phạm vi là ``~LEAD_ORDER_Q`` — mốc duy nhất là ĐÃ LÊN HÓA
        ĐƠN KiotViet, không còn phụ thuộc sale có gắn trạng thái hay chưa. Lead
        đã chốt nhưng chưa kịp lên hóa đơn CỐ Ý rớt khỏi tổng (đo prod 20/08:
        35 đơn ≈ 58 triệu trong tháng 8, −0,9%).
        """
        self._order("100000", status=0)  # lead đang chờ chốt, chưa gắn trạng thái sale
        self._order("70000", status=1)  # lead đã xác nhận nhưng chưa chốt trạng thái sale
        self._order("50000", status=6)  # lead đã đóng (hủy) → vẫn ngoài tổng
        self._closed("200000", status=3, invoice_status=None)  # lead ĐÃ CHỐT → vẫn ngoài tổng
        self._closed("300000", status=3)  # đã lên hóa đơn → vào tổng

        data = self._payload()
        self.assertEqual(data["total_revenue"], "300000.00")
        self.assertEqual(data["total_orders"], 1)
        # Nguồn/địa lý chỉ tính bucket tổng.
        self.assertEqual(
            [(r["sale_channel"], r["revenue"]) for r in data["revenue_by_sale_channel"]],
            [("off_marketplace", "300000.00")],
        )

    def test_return_flow_statuses_12_13_counted_as_returned(self):
        self._order("100000", status=12, invoice_status=3)  # chờ lấy hàng hoàn
        self._order("50000", status=13, invoice_status=3)  # chờ xác nhận hoàn
        self._order("200000", status=3, invoice_status=3)  # giao thành công

        data = self._payload()
        self.assertEqual(data["returned_revenue"], "150000.00")
        self.assertEqual(data["returned_orders"], 2)
        self.assertEqual(data["total_revenue"], "350000.00")
        self.assertEqual(data["total_orders"], 3)

    def test_dai_mo_branch_excluded_from_all_buckets(self):
        """Kho Đại Mỗ không tính doanh thu công ty (user chốt 2026-07-20) —
        khớp báo cáo bán hàng KiotViet + số thuần Lark vốn không gồm kho này."""
        self._order("100000", status=3, invoice_status=3, branch_id="1000000477")
        self._order("50000", status=0, branch_id="1000000477")  # lead cũng loại
        self._order("30000", status=5, invoice_status=3, branch_id="1000000477")
        self._order("200000", status=3, invoice_status=3)  # kho khác vẫn tính

        data = self._payload()
        self.assertEqual(data["total_revenue"], "200000.00")
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["returned_orders"], 0)

    def test_restored_cancelled_order_excluded_from_base(self):
        """Đơn hủy đã có bản khôi phục (clone) còn sống phải bị loại khỏi base:
        clone kế thừa created_at của đơn gốc nên rơi vào CÙNG kỳ báo cáo — nếu
        không loại, đơn gốc và clone cùng cộng vào total_revenue, đếm một đơn
        vật lý hai lần."""
        source = self._order("100000", status=0, invoice_status=2)
        self._order("100000", status=0, invoice_status=3, restored_from=source)

        data = self._payload()
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["total_revenue"], "100000.00")

    def test_cancelled_order_without_restore_excluded_from_total(self):
        self._order("50000", status=0, invoice_status=2)

        data = self._payload()
        self.assertEqual(data["total_orders"], 0)
        self.assertEqual(data["total_revenue"], "0")

    def test_cancelled_order_with_soft_deleted_restore_excluded_from_total(self):
        """Bản khôi phục bị xoá mềm (deleted_at) không còn "sống" — đơn hủy gốc
        coi như CHƯA khôi phục, vẫn phải bị loại khỏi tổng như bình thường."""
        source = self._order("60000", status=0, invoice_status=2)
        self._order(
            "60000",
            status=0,
            invoice_status=3,
            restored_from=source,
            deleted_at=timezone.now(),
        )

        data = self._payload()
        self.assertEqual(data["total_orders"], 0)
        self.assertEqual(data["total_revenue"], "0")

    def test_empty_period_returns_zeroes(self):
        data = OrderRepository.summary_overview(date_from="2026-01-01", date_to="2026-01-02")
        self.assertEqual(data["total_revenue"], "0")
        self.assertEqual(data["total_orders"], 0)
        self.assertEqual(data["return_risk_revenue"], "0")
        self.assertEqual(data["return_risk_orders"], 0)
        self.assertEqual(Decimal(data["ads_cost"]), Decimal("0"))
        self.assertEqual(Decimal(data["ads_cost_facebook"]), Decimal("0"))
        self.assertEqual(Decimal(data["ads_cost_google"]), Decimal("0"))
        self.assertEqual(data["revenue_by_sale_channel"], [])
        self.assertEqual(data["returned_by_sale_channel"], [])
        self.assertEqual(data["return_risk_by_sale_channel"], [])
        self.assertEqual(data["revenue_by_region"], [])
        self.assertEqual(data["revenue_by_province"], [])

    def test_four_buckets_sum_to_total_revenue(self):
        self._closed("100000", status=3)   # giao thành công
        self._closed("200000", status=0)   # chờ giao (Mới)
        self._closed("300000", status=2)   # chờ giao (Đang giao)
        self._closed("50000", status=4)    # hoàn
        self._closed("30000", status=13)   # hoàn (chờ xác nhận hoàn)
        self._closed("70000", status=10)   # rủi ro hoàn (chờ giao lại)

        data = self._payload()
        self.assertEqual(data["total_revenue"], "750000.00")
        self.assertEqual(data["total_orders"], 6)
        self.assertEqual(data["delivered_revenue"], "100000.00")
        self.assertEqual(data["awaiting_revenue"], "500000.00")
        self.assertEqual(data["awaiting_orders"], 2)
        self.assertEqual(data["returned_revenue"], "80000.00")
        self.assertEqual(data["return_risk_revenue"], "70000.00")
        self.assertEqual(data["return_risk_orders"], 1)
        # Bất biến: 4 rổ cộng lại = tổng, cả tiền lẫn số đơn.
        parts = ("delivered", "awaiting", "returned", "return_risk")
        self.assertEqual(
            sum(Decimal(data[f"{p}_revenue"]) for p in parts), Decimal(data["total_revenue"])
        )
        self.assertEqual(sum(data[f"{p}_orders"] for p in parts), data["total_orders"])

    def test_unknown_status_from_kiotviet_lands_in_awaiting_bucket(self):
        """status=14 không thuộc OrderStatus enum (mã KiotViet thêm mới, sync
        ghi thẳng int(status_val) không qua whitelist — xem
        _import_service_upsert_mixin.py) vẫn phải rơi vào ĐÚNG MỘT rổ.
        awaiting_q dùng phép phủ định (không liệt kê dương) nên rổ "chờ giao"
        tự nhận mã lạ này, giữ bất biến 4 rổ = tổng."""
        self._closed("100000", status=3)  # giao thành công
        self._closed("50000", status=4)  # hoàn
        self._closed("70000", status=10)  # rủi ro hoàn
        self._closed("999", status=14)  # mã trạng thái lạ, ngoài enum

        data = self._payload()
        self.assertEqual(data["total_revenue"], "220999.00")
        self.assertEqual(data["total_orders"], 4)
        self.assertEqual(data["awaiting_revenue"], "999.00")
        self.assertEqual(data["awaiting_orders"], 1)
        parts = ("delivered", "awaiting", "returned", "return_risk")
        self.assertEqual(
            sum(Decimal(data[f"{p}_revenue"]) for p in parts), Decimal(data["total_revenue"])
        )
        self.assertEqual(sum(data[f"{p}_orders"] for p in parts), data["total_orders"])

    def test_blocks_follow_total_revenue_basis(self):
        self._closed("100000", status=3, source="landing")
        self._closed("200000", status=0, source="shopee")
        self._closed("50000", status=4, source="landing")
        self._closed("70000", status=10, source="tiktok")

        data = self._payload()
        # Khối doanh thu = TỔNG (gồm cả hoàn + rủi ro hoàn), không còn là "net".
        self.assertEqual(
            data["revenue_by_sale_channel"],
            [
                {
                    "sale_channel": "marketplace",
                    "revenue": "270000.00",
                    "orders": 2,
                    "delivered_revenue": "0",
                    "delivered_orders": 0,
                },
                {
                    "sale_channel": "off_marketplace",
                    "revenue": "150000.00",
                    "orders": 2,
                    "delivered_revenue": "100000.00",
                    "delivered_orders": 1,
                },
            ],
        )
        self.assertEqual(
            data["return_risk_by_sale_channel"],
            [{"sale_channel": "marketplace", "revenue": "70000.00", "orders": 1}],
        )
        self.assertNotIn("cancelled_by_sale_channel", data)
        # Bất biến: tổng mọi khối = ô KPI tương ứng.
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_sale_channel"]),
            Decimal(data["total_revenue"]),
        )
        # Hai khối ĐỊA LÝ ở cơ sở THUẦN, không phải tổng.
        net = Decimal(data["total_revenue"]) - Decimal(data["returned_revenue"])
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_region"]), net
        )
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_province"]), net
        )

    def test_scope_takes_every_invoiced_order_whatever_the_sale_status(self):
        """Mốc DUY NHẤT là đã lên hóa đơn KiotViet — trạng thái sale không còn gác.

        Gồm cả đơn có hóa đơn mà trạng thái sale nằm NGOÀI 4 mã chốt (trước đây
        bị loại), lẫn 3 dạng "chưa gắn trạng thái" của dữ liệu thật.
        """
        for code in ("closed", "closed_express", "closed_scheduled_date", "created_order"):
            self._closed("100000", status=3, sales_status=code)
        # Trạng thái sale ngoài 4 mã chốt — TRƯỚC đây bị loại, nay vào tổng.
        self._closed("50000", status=3, sales_status="customer_bombed")
        # Đã có hóa đơn nhưng chưa gắn trạng thái sale — 3 dạng dữ liệu thật.
        self._closed("10000", status=3, sales_status=None)
        self._closed("20000", status=3, sales_status="")
        order = self._order("30000", status=3, invoice_status=3)
        OrderAssessment.objects.create(order=order, sales_status_code=None)
        # Lead sale đã chốt nhưng CHƯA lên hóa đơn — nay rớt khỏi tổng.
        self._closed("400000", status=3, sales_status="closed", invoice_status=None)

        data = self._payload()
        self.assertEqual(data["total_revenue"], "510000.00")
        self.assertEqual(data["total_orders"], 8)

    def test_scope_drops_only_cancelled_and_leads(self):
        """Sau 2026-08-20 chỉ còn HAI lý do bị loại: đơn hủy và lead chưa có hóa đơn."""
        self._closed("100000", status=3)                                   # đếm
        self._closed("900000", status=4, sales_status="customer_bombed")   # ĐẾM: có hóa đơn
        self._closed("700000", status=0, sales_status="closed", invoice_status=2)  # loại: hóa đơn hủy
        self._closed("600000", status=6)                                   # loại: đơn đã hủy
        self._closed("800000", status=3, sales_status="not_buying", invoice_status=None)  # loại: lead
        self._order("500000", status=0)                                    # loại: lead

        data = self._payload()
        self.assertEqual(data["total_revenue"], "1000000.00")
        self.assertEqual(data["total_orders"], 2)
        self.assertEqual(data["returned_revenue"], "900000.00")
        self.assertNotIn("cancelled_revenue", data)
        self.assertNotIn("pending_revenue", data)
        self.assertNotIn("gross_revenue", data)


class OverviewProductFilterTests(TestCase):
    """Báo cáo tổng — bộ lọc theo sản phẩm.

    Quy ước (user chốt 2026-07-20): khi lọc SP, mọi số tiền là tiền CỦA RIÊNG sản
    phẩm đó (``OrderItem.line_total``), không phải giá trị cả đơn. Nhờ vậy cộng số
    liệu của mọi SP lại đúng bằng tổng, không đếm trùng đơn mua kèm.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview-product")
        self.channel = Channel.objects.create(id=930001, code="ov-p-ch", name="OV Product")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000002", phone_normalized="0900000002"
        )
        self.pa = Product.objects.create(name="Ghế massage A", sku="SKU-A", price=Decimal("100"))
        self.pb = Product.objects.create(name="Đệm B", sku="SKU-B", price=Decimal("50"))

    def _order(self, *, lines, status=0, invoice_status=3, source="landing", province="Hà Nội"):
        total = sum(Decimal(amount) for _, amount in lines)
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000002",
            status=status,
            invoice_status=invoice_status,
            order_source=source,
            province=province,
            total_after_discount=Decimal(total),
        )
        for product, amount in lines:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=1,
                unit_price=Decimal(amount),
                line_total=Decimal(amount),
            )
        return order

    def _payload(self, query=""):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_filter_counts_only_that_product_amount(self):
        # Đơn mua kèm: A 300k + B 200k. Lọc A chỉ được tính 300k.
        self._order(lines=[(self.pa, "300000"), (self.pb, "200000")], status=3)
        self._order(lines=[(self.pb, "150000")], status=3)

        data = self._payload(f"?product_ids={self.pa.id}")
        self.assertEqual(data["total_revenue"], "300000.00")
        self.assertEqual(data["total_orders"], 1)  # đếm theo ĐƠN, không theo dòng
        self.assertEqual(data["delivered_revenue"], "300000.00")

        data_b = self._payload(f"?product_ids={self.pb.id}")
        self.assertEqual(data_b["total_revenue"], "350000.00")
        self.assertEqual(data_b["total_orders"], 2)

    def test_sum_over_products_equals_unfiltered_total(self):
        self._order(lines=[(self.pa, "300000"), (self.pb, "200000")], status=3)
        self._order(lines=[(self.pb, "150000")], status=0)

        total = Decimal(self._payload()["total_revenue"])
        by_a = Decimal(self._payload(f"?product_ids={self.pa.id}")["total_revenue"])
        by_b = Decimal(self._payload(f"?product_ids={self.pb.id}")["total_revenue"])
        # Không đếm trùng: tiền của A + tiền của B = tổng doanh thu.
        self.assertEqual(by_a + by_b, total)

    def test_multi_select_sums_selected_products_only(self):
        self._order(lines=[(self.pa, "300000"), (self.pb, "200000")], status=3)

        data = self._payload(f"?product_ids={self.pa.id},{self.pb.id}")
        self.assertEqual(data["total_revenue"], "500000.00")
        self.assertEqual(data["total_orders"], 1)

    def test_filter_applies_to_every_block(self):
        self._order(lines=[(self.pa, "300000"), (self.pb, "900000")], status=3, source="shopee")
        self._order(lines=[(self.pa, "100000")], status=5, source="landing")  # hoàn
        self._order(lines=[(self.pa, "50000")], status=0, invoice_status=2)  # hủy → loại khỏi mọi khối

        data = self._payload(f"?product_ids={self.pa.id}")
        self.assertEqual(data["total_revenue"], "400000.00")
        self.assertEqual(data["total_orders"], 2)
        self.assertEqual(data["returned_revenue"], "100000.00")
        # Khối theo nguồn / vùng / tỉnh cũng chỉ tính tiền của A.
        self.assertEqual(
            data["revenue_by_sale_channel"],
            [
                {
                    "sale_channel": "marketplace",
                    "revenue": "300000.00",
                    "orders": 1,
                    "delivered_revenue": "300000.00",
                    "delivered_orders": 1,
                },
                {
                    "sale_channel": "off_marketplace",
                    "revenue": "100000.00",
                    "orders": 1,
                    "delivered_revenue": "0",
                    "delivered_orders": 0,
                },
            ],
        )
        # Khối địa lý ở cơ sở THUẦN → 100k của đơn hoàn không vào.
        self.assertEqual(
            [(r["region"], r["revenue"]) for r in data["revenue_by_region"]],
            [("red_river_delta", "300000.00")],
        )
        # Bất biến: tổng mọi khối vẫn khớp KPI khi đang lọc.
        for total_key, rows_key in (
            ("total_revenue", "revenue_by_sale_channel"),
            ("returned_revenue", "returned_by_sale_channel"),
        ):
            self.assertEqual(
                sum(Decimal(row["revenue"]) for row in data[rows_key]),
                Decimal(data[total_key]),
                f"{rows_key} phải khớp {total_key} khi lọc sản phẩm",
            )
        # Khối địa lý ở cơ sở thuần — lọc SP vẫn phải giữ đúng bất biến đó.
        net = Decimal(data["total_revenue"]) - Decimal(data["returned_revenue"])
        for rows_key in ("revenue_by_region", "revenue_by_province"):
            self.assertEqual(
                sum(Decimal(row["revenue"]) for row in data[rows_key]),
                net,
                f"{rows_key} phải khớp doanh thu thuần khi lọc sản phẩm",
            )

    def test_orders_without_the_product_are_excluded(self):
        self._order(lines=[(self.pb, "200000")], status=3)

        data = self._payload(f"?product_ids={self.pa.id}")
        self.assertEqual(data["total_revenue"], "0")
        self.assertEqual(data["total_orders"], 0)
        self.assertEqual(data["revenue_by_sale_channel"], [])

    def test_ads_cost_not_filtered_by_product(self):
        """Chi phí Ads không tách được theo SP → giữ nguyên tổng (FE ghi chú).

        Phải lọc ngoại sàn, nếu không cả hai vế đều là 0 và test rỗng nghĩa.
        """
        seed_ads_spend()
        self._order(lines=[(self.pa, "300000")], status=3)
        base = "?date_from=2026-07-01&date_to=2026-07-31&sale_channel=off_marketplace"
        unfiltered = self._payload(base)
        filtered = self._payload(f"{base}&product_ids={self.pa.id}")
        for field in ("ads_cost", "ads_cost_facebook", "ads_cost_google"):
            self.assertEqual(filtered[field], unfiltered[field])
        self.assertEqual(Decimal(unfiltered["ads_cost_facebook"]), Decimal("110.00"))

    def test_invalid_product_id_ignored(self):
        self._order(lines=[(self.pa, "300000")], status=3)

        # "abc" bị bỏ qua, còn lại id hợp lệ → vẫn lọc bình thường.
        data = self._payload(f"?product_ids=abc,{self.pa.id}")
        self.assertEqual(data["total_revenue"], "300000.00")
        # Toàn giá trị rác → coi như không lọc.
        self.assertEqual(self._payload("?product_ids=abc")["total_revenue"], "300000.00")

    def test_repository_accepts_product_ids_directly(self):
        self._order(lines=[(self.pa, "300000"), (self.pb, "200000")], status=3)

        data = OrderRepository.summary_overview(product_ids=[self.pa.id])
        self.assertEqual(data["total_revenue"], "300000.00")


class OverviewSaleChannelTests(TestCase):
    """Báo cáo tổng — NĂM nhóm kênh bán của biểu đồ + bộ lọc 2 nhóm lớn.

    User chốt 2026-08-20 (thay 2 nhóm nội/ngoại của 2026-07-20): biểu đồ chia
    Shopee · TikTok · Facebook · Web · Ngoại sàn khác. Sàn nhận theo order_source
    HOẶC prefix mã hóa đơn HDSPE_/HDTTS_; Facebook/Web nhận theo CODE kênh.

    Ô LỌC "Kênh bán" vẫn chỉ 2 giá trị marketplace/off_marketplace — nó là thứ
    gác 2 ô chi phí Ads nên cố ý giữ thô.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview-sale-channel")
        self.channel = Channel.objects.create(id=940001, code="ov-sc", name="OV SaleChannel")
        self.fb_channel = Channel.objects.create(
            id=940002, code="facebook-test", name="Facebook Test"
        )
        # get_or_create: code là unique trong tập kênh active và migration có thể
        # đã seed sẵn kênh này — tạo thẳng sẽ vỡ ở môi trường có seed.
        self.web_channel, _ = Channel.objects.get_or_create(
            code="google-ads-web", defaults={"name": "Google Ads/Web"}
        )
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000003", phone_normalized="0900000003"
        )

    def _order(
        self, total, *, source="landing", invoice_code="", status=3, invoice_status=3,
        channel=None,
    ):
        return Order.objects.create(
            channel=channel or self.channel,
            customer=self.customer,
            name="X",
            phone="0900000003",
            status=status,
            invoice_status=invoice_status,
            order_source=source,
            invoice_code=invoice_code,
            province="Hà Nội",
            total_after_discount=Decimal(total),
        )

    def _payload(self, query=""):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_splits_shopee_tiktok_facebook_web_and_the_rest(self):
        self._order("300000", source="shopee")
        self._order("200000", source="tiktok")
        self._order("120000", source="landing", channel=self.fb_channel)
        self._order("110000", source="landing", channel=self.web_channel)
        self._order("100000", source="landing")
        self._order("50000", source="messenger")
        self._order("20000", source=None)  # không nguồn → ngoại sàn khác

        data = self._payload()
        # Không lọc kênh → tầng "Tất cả": chỉ 2 nhóm lớn.
        self.assertEqual(
            [
                (r["sale_channel"], r["revenue"], r["orders"])
                for r in data["revenue_by_sale_channel"]
            ],
            [
                ("marketplace", "500000.00", 2),
                ("off_marketplace", "400000.00", 5),
            ],
        )
        # Bất biến: 5 nhóm cộng lại đúng bằng ô KPI.
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_sale_channel"]),
            Decimal(data["total_revenue"]),
        )

    def test_marketplace_wins_over_channel(self):
        """Đơn Shopee treo trên kênh Facebook vẫn là Shopee — thứ tự khớp là nghiệp vụ.

        Trên prod kênh `delax-chinh-hang` mang 786 đơn Shopee và prefix HDSPE_
        nằm trên 6 kênh khác nhau (đo 08/2026). Đảo thứ tự nhánh là gán nhầm
        ~1,8 tỉ doanh thu Shopee sang Facebook mà không có gì báo lỗi.
        """
        self._order("300000", source="shopee", channel=self.fb_channel)
        self._order("200000", source=None, invoice_code="HDTTS_123", channel=self.web_channel)

        # Lọc nội sàn để thấy nhóm con — ở tầng "Tất cả" cả hai đã gộp thành 1 lát.
        data = self._payload("?sale_channel=marketplace")
        self.assertEqual(
            [(r["sale_channel"], r["revenue"]) for r in data["revenue_by_sale_channel"]],
            [("shopee", "300000.00"), ("tiktok", "200000.00")],
        )

    def test_facebook_matches_any_channel_code_starting_with_facebook(self):
        """Mở kênh facebook-<tên> mới là TỰ vào nhóm, không phải sửa danh sách id."""
        other_fb = Channel.objects.create(id=940004, code="facebook-moi", name="Facebook Mới")
        self._order("300000", channel=self.fb_channel)
        self._order("200000", channel=other_fb)

        data = self._payload("?sale_channel=off_marketplace")
        self.assertEqual(
            [
                (r["sale_channel"], r["revenue"], r["orders"])
                for r in data["revenue_by_sale_channel"]
            ],
            [("facebook", "500000.00", 2)],
        )

    def test_invoice_prefix_picks_the_right_marketplace_without_source(self):
        """Đơn sàn bị sót order_source vẫn phải vào đúng sàn (2026-07 có 3 đơn)."""
        self._order("300000", source=None, invoice_code="HDSPE_26070712AB")
        self._order("200000", source=None, invoice_code="HDTTS_5848985132")
        self._order("100000", source=None, invoice_code="HD012345")

        data = self._payload()
        self.assertEqual(
            [(r["sale_channel"], r["revenue"]) for r in data["revenue_by_sale_channel"]],
            [("marketplace", "500000.00"), ("off_marketplace", "100000.00")],
        )

    def test_fixed_order_not_sorted_by_value(self):
        """Thứ tự lát cố định nội → ngoại, không đảo theo giá trị."""
        self._order("10000", source="shopee")
        self._order("900000", source="landing")
        self._order("500000", channel=self.web_channel)
        self._order("20000", channel=self.fb_channel)

        # Tầng "Tất cả": nội sàn 10k đứng TRƯỚC ngoại sàn 1,42tr dù nhỏ hơn nhiều.
        self.assertEqual(
            [r["sale_channel"] for r in self._payload()["revenue_by_sale_channel"]],
            ["marketplace", "off_marketplace"],
        )
        # Tầng ngoại sàn giữ đúng thứ tự Facebook → Web → Khác.
        self.assertEqual(
            [
                r["sale_channel"]
                for r in self._payload("?sale_channel=off_marketplace")[
                    "revenue_by_sale_channel"
                ]
            ],
            ["facebook", "web", "off_other"],
        )

    def test_filter_marketplace(self):
        self._order("300000", source="shopee")
        self._order("100000", source="landing")

        data = self._payload("?sale_channel=marketplace")
        self.assertEqual(data["total_revenue"], "300000.00")
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual([r["sale_channel"] for r in data["revenue_by_sale_channel"]], ["shopee"])

    def test_filter_off_marketplace(self):
        self._order("300000", source="shopee")
        self._order("100000", source="landing")
        self._order("50000", source=None, invoice_code="HDSPE_2607071X")  # nội sàn theo prefix

        data = self._payload("?sale_channel=off_marketplace")
        self.assertEqual(data["total_revenue"], "100000.00")
        self.assertEqual(
            [r["sale_channel"] for r in data["revenue_by_sale_channel"]], ["off_other"]
        )

    def test_filter_applies_to_geo_blocks_and_keeps_totals(self):
        self._order("300000", source="shopee")
        self._order("100000", source="landing")

        data = self._payload("?sale_channel=marketplace")
        net = Decimal(data["total_revenue"]) - Decimal(data["returned_revenue"])
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_region"]), net)
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_province"]), net)

    def test_unknown_filter_value_means_no_filter(self):
        self._order("300000", source="shopee")
        self._order("100000", source="landing")

        self.assertEqual(self._payload("?sale_channel=abc")["total_revenue"], "400000.00")
        self.assertEqual(self._payload("?sale_channel=")["total_revenue"], "400000.00")

    def test_filter_combines_with_product_filter(self):
        product = Product.objects.create(name="Ghế A", sku="SKU-SC-A", price=Decimal("100"))
        shopee_order = self._order("300000", source="shopee")
        OrderItem.objects.create(
            order=shopee_order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=Decimal("120000"),
            line_total=Decimal("120000"),
        )
        landing_order = self._order("100000", source="landing")
        OrderItem.objects.create(
            order=landing_order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=Decimal("100000"),
            line_total=Decimal("100000"),
        )

        data = self._payload(f"?sale_channel=marketplace&product_ids={product.id}")
        # Chỉ đơn nội sàn, và chỉ phần tiền của sản phẩm trong đơn đó.
        self.assertEqual(data["total_revenue"], "120000.00")
        self.assertEqual(data["total_orders"], 1)


class OverviewSaleChannelGroupFilterTests(TestCase):
    """Ô lọc thứ hai "Kênh bán" — thu hẹp về ĐÚNG một nhóm của tầng đang chọn.

    User chốt 2026-08-24: ô cũ đổi tên thành "Nguồn" (marketplace/off_marketplace,
    giữ nguyên giá trị), thêm ô "Kênh bán" lấy đúng danh sách nhóm mà biểu đồ
    ĐANG hiển thị (``sale_channel_groups_for``) — Tất cả → nội/ngoại sàn, Nội sàn
    → Shopee/TikTok, Ngoại sàn → Facebook/Web/Ngoại sàn khác.

    Chọn một nhóm thì 3 donut còn ĐÚNG MỘT lát 100% và biểu đồ diễn biến còn một
    series: "biểu đồ luôn là dữ liệu đang lọc". Hệ quả CỐ Ý: ở tầng Tất cả chọn
    "Ngoại sàn" cho số y hệt ô Nguồn = Ngoại sàn nhưng donut không tách nhóm con.

    Điều kiện lọc phải là PHẦN BÙ LUỸ TIẾN khớp từng nhánh của ``Case`` gán nhóm
    (sàn xét TRƯỚC kênh) — lệch một nhánh là tổng donut không còn bằng ô KPI.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview-group")
        self.channel = Channel.objects.create(id=960001, code="ov-grp", name="OV Group")
        self.fb_channel = Channel.objects.create(
            id=960002, code="facebook-grp", name="Facebook Group"
        )
        # get_or_create vì migration có thể đã seed kênh này (code là unique).
        self.web_channel, _ = Channel.objects.get_or_create(
            code="google-ads-web", defaults={"name": "Google Ads/Web"}
        )
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000005", phone_normalized="0900000005"
        )

    def _order(self, total, *, source="landing", invoice_code="", status=3, channel=None):
        return Order.objects.create(
            channel=channel or self.channel,
            customer=self.customer,
            name="X",
            phone="0900000005",
            status=status,
            invoice_status=3,
            order_source=source,
            invoice_code=invoice_code,
            province="Hà Nội",
            total_after_discount=Decimal(total),
        )

    def _seed_five_groups(self):
        """Một đơn cho mỗi nhóm lá: nội sàn 500k, ngoại sàn 330k, tổng 830k."""
        self._order("300000", source="shopee")
        self._order("200000", source="tiktok")
        self._order("120000", channel=self.fb_channel)
        self._order("110000", channel=self.web_channel)
        self._order("100000", source="landing")

    def _payload(self, query=""):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _rows(self, data):
        return [(r["sale_channel"], r["revenue"]) for r in data["revenue_by_sale_channel"]]

    def test_narrows_to_a_single_group_of_each_level(self):
        """Mỗi nhóm lá lọc đúng tập đơn của nó và donut còn đúng một lát."""
        cases = (
            ("?sale_channel=marketplace&sale_channel_group=shopee", "shopee", "300000.00"),
            ("?sale_channel=marketplace&sale_channel_group=tiktok", "tiktok", "200000.00"),
            ("?sale_channel=off_marketplace&sale_channel_group=facebook", "facebook", "120000.00"),
            ("?sale_channel=off_marketplace&sale_channel_group=web", "web", "110000.00"),
            (
                "?sale_channel=off_marketplace&sale_channel_group=off_other",
                "off_other",
                "100000.00",
            ),
            # Tầng "Tất cả": hai nhóm lớn cũng là giá trị hợp lệ của ô Kênh bán.
            ("?sale_channel_group=marketplace", "marketplace", "500000.00"),
            ("?sale_channel_group=off_marketplace", "off_marketplace", "330000.00"),
        )
        self._seed_five_groups()

        for query, group, revenue in cases:
            with self.subTest(group=group):
                data = self._payload(query)
                self.assertEqual(data["total_revenue"], revenue)
                self.assertEqual(self._rows(data), [(group, revenue)])

    def test_every_group_keeps_donut_total_equal_to_kpi(self):
        """Bất biến sống còn: tổng 3 donut = ô KPI tương ứng, ở MỌI nhóm."""
        self._seed_five_groups()
        self._order("40000", source="shopee", status=5)  # hoàn
        self._order("60000", channel=self.fb_channel, status=10)  # rủi ro hoàn

        for query in (
            "?sale_channel=marketplace&sale_channel_group=shopee",
            "?sale_channel=off_marketplace&sale_channel_group=facebook",
            "?sale_channel_group=off_marketplace",
        ):
            data = self._payload(query)
            for total_key, rows_key in (
                ("total_revenue", "revenue_by_sale_channel"),
                ("returned_revenue", "returned_by_sale_channel"),
                ("return_risk_revenue", "return_risk_by_sale_channel"),
            ):
                with self.subTest(query=query, block=rows_key):
                    self.assertEqual(
                        sum(Decimal(row["revenue"]) for row in data[rows_key]),
                        Decimal(data[total_key]),
                    )

    def test_marketplace_still_wins_over_channel(self):
        """Đơn Shopee treo trên kênh Facebook KHÔNG được lọt vào nhóm facebook.

        Đây là bẫy ~1,8 tỉ đã ghi ở ``SALE_CHANNEL_GROUPS``: điều kiện lọc phải
        loại sàn TRƯỚC khi xét code kênh, y hệt thứ tự nhánh của ``Case``.
        """
        self._order("300000", source="shopee", channel=self.fb_channel)
        self._order("120000", channel=self.fb_channel)

        data = self._payload("?sale_channel=off_marketplace&sale_channel_group=facebook")
        self.assertEqual(data["total_revenue"], "120000.00")
        self.assertEqual(data["total_orders"], 1)

    def test_web_excludes_facebook_and_marketplace(self):
        """Nhóm `web` là phần bù luỹ tiến: không sàn, không facebook, đúng code web."""
        self._order("300000", source="shopee", channel=self.web_channel)
        self._order("120000", channel=self.fb_channel)
        self._order("110000", channel=self.web_channel)

        data = self._payload("?sale_channel=off_marketplace&sale_channel_group=web")
        self.assertEqual(data["total_revenue"], "110000.00")

    def test_group_outside_the_current_level_is_ignored(self):
        """Nhóm không thuộc tầng đang chọn coi như không lọc — khớp cách xử lý `sale_channel`."""
        self._seed_five_groups()

        data = self._payload("?sale_channel=marketplace&sale_channel_group=facebook")
        self.assertEqual(data["total_revenue"], "500000.00")
        self.assertEqual(self._rows(data), [("shopee", "300000.00"), ("tiktok", "200000.00")])

    def test_unknown_or_empty_group_means_no_filter(self):
        self._seed_five_groups()

        for query in ("?sale_channel_group=abc", "?sale_channel_group="):
            with self.subTest(query=query):
                self.assertEqual(self._payload(query)["total_revenue"], "830000.00")

    def test_filter_applies_to_geo_blocks(self):
        self._seed_five_groups()

        data = self._payload("?sale_channel=off_marketplace&sale_channel_group=facebook")
        net = Decimal(data["total_revenue"]) - Decimal(data["returned_revenue"])
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_region"]), net)
        self.assertEqual(sum(Decimal(r["revenue"]) for r in data["revenue_by_province"]), net)

    def test_timeseries_keeps_only_the_selected_group(self):
        self._seed_five_groups()

        series = self._payload("?sale_channel=off_marketplace&sale_channel_group=web")[
            "revenue_timeseries"
        ]
        for grain in ("by_day", "by_hour", "by_weekday"):
            with self.subTest(grain=grain):
                self.assertEqual({row["sale_channel"] for row in series[grain]}, {"web"})
                self.assertEqual(
                    sum(Decimal(row["revenue"]) for row in series[grain]), Decimal("110000.00")
                )

    def test_ads_cost_narrows_to_the_selected_channel(self):
        """Chọn kênh lá thì chỉ ô chi phí của ĐÚNG kênh đó có số (user chốt 2026-08-24).

        FB 100×1.1 = 110 ; Google 200×1.08 = 216. `off_other` (Resale/KOC/quầy)
        không tiêu tiền quảng cáo nên cả hai ô về 0.
        """
        seed_ads_spend()
        period = "?date_from=2026-07-01&date_to=2026-07-31"
        cases = (
            (f"{period}&sale_channel=off_marketplace", "110.00", "216.00"),
            (f"{period}&sale_channel_group=off_marketplace", "110.00", "216.00"),
            (f"{period}&sale_channel=off_marketplace&sale_channel_group=facebook", "110.00", "0"),
            (f"{period}&sale_channel=off_marketplace&sale_channel_group=web", "0", "216.00"),
            (f"{period}&sale_channel=off_marketplace&sale_channel_group=off_other", "0", "0"),
            (f"{period}&sale_channel=marketplace&sale_channel_group=shopee", "0", "0"),
            (f"{period}&sale_channel_group=marketplace", "0", "0"),
            (period, "0", "0"),
        )

        for query, facebook, google in cases:
            with self.subTest(query=query):
                data = self._payload(query)
                self.assertEqual(Decimal(data["ads_cost_facebook"]), Decimal(facebook))
                self.assertEqual(Decimal(data["ads_cost_google"]), Decimal(google))
                self.assertEqual(Decimal(data["ads_cost"]), Decimal(facebook) + Decimal(google))

    def test_combines_with_product_filter(self):
        product = Product.objects.create(name="Ghế G", sku="SKU-GRP-A", price=Decimal("100"))
        for order, line_total in (
            (self._order("300000", source="shopee"), "120000"),
            (self._order("110000", channel=self.web_channel), "90000"),
        ):
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=1,
                unit_price=Decimal(line_total),
                line_total=Decimal(line_total),
            )

        data = self._payload(
            f"?sale_channel=off_marketplace&sale_channel_group=web&product_ids={product.id}"
        )
        self.assertEqual(data["total_revenue"], "90000.00")
        self.assertEqual(data["total_orders"], 1)


class OverviewSaleChannelLevelTests(SimpleTestCase):
    """Bảng TẦNG nhóm kênh bán phải phủ kín và không chồng nhau.

    Thêm một nhóm lá mới mà quên xếp vào tầng nào sẽ làm test này đỏ ngay, thay
    vì âm thầm biến mất khỏi mọi biểu đồ trên prod.
    """

    def test_every_leaf_group_belongs_to_exactly_one_child_level(self):
        from apps.orders.reporting.shared import (
            SALE_CHANNEL_GROUP_LEVELS,
            SALE_CHANNEL_GROUPS,
            SALE_CHANNEL_MARKETPLACE,
            SALE_CHANNEL_OFF_MARKETPLACE,
        )

        children = [
            SALE_CHANNEL_GROUP_LEVELS[SALE_CHANNEL_MARKETPLACE],
            SALE_CHANNEL_GROUP_LEVELS[SALE_CHANNEL_OFF_MARKETPLACE],
        ]
        flat = [group for level in children for group in level]
        self.assertEqual(len(flat), len(set(flat)), "một nhóm lá nằm ở hai tầng")
        self.assertEqual(set(flat), set(SALE_CHANNEL_GROUPS))

    def test_rollup_maps_every_leaf_to_a_top_level_group(self):
        from apps.orders.reporting.shared import (
            SALE_CHANNEL_GROUP_LEVELS,
            SALE_CHANNEL_GROUPS,
            rolled_sale_channel,
        )

        top = set(SALE_CHANNEL_GROUP_LEVELS[""])
        for group in SALE_CHANNEL_GROUPS:
            self.assertIn(rolled_sale_channel(group, ""), top)
            # Đã lọc nội/ngoại sàn thì KHÔNG gộp nữa — giữ nguyên nhóm con.
            self.assertEqual(rolled_sale_channel(group, "marketplace"), group)
            self.assertEqual(rolled_sale_channel(group, "off_marketplace"), group)


class OverviewRevenueTimeseriesTests(TestCase):
    """Biểu đồ diễn biến — cơ sở DOANH THU THUẦN, 3 mức từ CÙNG một tập đơn."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview-timeseries")
        self.channel = Channel.objects.create(id=950001, code="ov-ts", name="OV Timeseries")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000004", phone_normalized="0900000004"
        )

    def _order(self, total, *, when, source="landing", status=3):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000004",
            status=status,
            invoice_status=3,
            order_source=source,
            province="Hà Nội",
            total_after_discount=Decimal(total),
        )
        Order.objects.filter(pk=order.pk).update(created_at=when)
        return order

    def _payload(self, query=""):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()["revenue_timeseries"]

    @staticmethod
    def _local(year, month, day, hour):
        """Mốc giờ ĐỊA PHƯƠNG (Asia/Bangkok, UTC+7) — không phải UTC."""
        return timezone.make_aware(timezone.datetime(year, month, day, hour, 0))

    def test_three_grains_come_from_the_same_orders(self):
        self._order("100000", when=self._local(2026, 7, 6, 9), source="shopee")  # Thứ Hai
        self._order("50000", when=self._local(2026, 7, 6, 9), source="landing")
        self._order("70000", when=self._local(2026, 7, 8, 21), source="landing")  # Thứ Tư

        data = self._payload("?date_from=2026-07-01&date_to=2026-07-31")
        totals = {
            grain: sum(Decimal(row["revenue"]) for row in data[grain])
            for grain in ("by_day", "by_hour", "by_weekday")
        }
        self.assertEqual(totals["by_day"], Decimal("220000.00"))
        # Ba mức chỉ khác cách gộp — tổng phải bằng nhau tuyệt đối.
        self.assertEqual(totals["by_hour"], totals["by_day"])
        self.assertEqual(totals["by_weekday"], totals["by_day"])

    def test_buckets_use_local_time_not_utc(self):
        """Đơn lúc 21h ngày 08/07 giờ VN phải nằm ở giờ 21 ngày 08, không phải 14h."""
        self._order("70000", when=self._local(2026, 7, 8, 21))

        data = self._payload("?date_from=2026-07-01&date_to=2026-07-31")
        self.assertEqual([r["bucket"] for r in data["by_day"]], ["2026-07-08"])
        self.assertEqual([r["bucket"] for r in data["by_hour"]], ["21"])
        # 08/07/2026 là Thứ Tư → isoweekday 3.
        self.assertEqual([r["bucket"] for r in data["by_weekday"]], ["3"])

    def test_returned_orders_are_out(self):
        """Cơ sở là doanh thu THUẦN — đơn hoàn không vào; rủi ro hoàn thì có."""
        self._order("100000", when=self._local(2026, 7, 6, 9))
        self._order("900000", when=self._local(2026, 7, 6, 10), status=5)  # hoàn
        self._order("25000", when=self._local(2026, 7, 6, 11), status=10)  # rủi ro hoàn

        data = self._payload("?date_from=2026-07-01&date_to=2026-07-31")
        self.assertEqual(
            sum(Decimal(row["revenue"]) for row in data["by_day"]), Decimal("125000.00")
        )

    def test_groups_follow_the_filter_level(self):
        self._order("100000", when=self._local(2026, 7, 6, 9), source="shopee")
        self._order("60000", when=self._local(2026, 7, 6, 9), source="tiktok")
        self._order("40000", when=self._local(2026, 7, 6, 9), source="landing")
        period = "?date_from=2026-07-01&date_to=2026-07-31"

        # Tầng "Tất cả" — gộp về 2 nhóm lớn.
        self.assertEqual(
            [(r["sale_channel"], r["revenue"]) for r in self._payload(period)["by_day"]],
            [("marketplace", "160000.00"), ("off_marketplace", "40000.00")],
        )
        # Tầng nội sàn — tách Shopee / TikTok, đúng thứ tự.
        self.assertEqual(
            [
                (r["sale_channel"], r["revenue"])
                for r in self._payload(f"{period}&sale_channel=marketplace")["by_day"]
            ],
            [("shopee", "100000.00"), ("tiktok", "60000.00")],
        )

    def test_only_buckets_with_data(self):
        """BE không sinh tích chéo bucket × nhóm — FE tự dựng trục và điền 0."""
        self._order("100000", when=self._local(2026, 7, 6, 9))

        data = self._payload("?date_from=2026-07-01&date_to=2026-07-31")
        self.assertEqual(len(data["by_day"]), 1)
        self.assertEqual(len(data["by_hour"]), 1)
        self.assertEqual(len(data["by_weekday"]), 1)

    def test_empty_period_returns_empty_series(self):
        data = self._payload("?date_from=2026-01-01&date_to=2026-01-02")
        self.assertEqual(data, {"by_day": [], "by_hour": [], "by_weekday": []})


class OverviewStatusBucketConstantsTests(SimpleTestCase):
    """4 rổ của Báo cáo tổng phải phủ kín mọi mã trạng thái, không chồng nhau.

    Thêm một mã trạng thái mới vào OrderStatus mà quên xếp rổ sẽ làm test này đỏ
    ngay, thay vì âm thầm rơi khỏi doanh thu trên prod.
    """

    def test_four_buckets_partition_every_order_status(self):
        from apps.orders.models import OrderStatus
        from apps.orders._repositories_reports import (
            AWAITING_DELIVERY_ORDER_STATUSES,
            DELIVERED_SUCCESS_ORDER_STATUS,
            RETURN_ORDER_STATUSES,
            RETURN_RISK_ORDER_STATUSES,
        )

        buckets = [
            {DELIVERED_SUCCESS_ORDER_STATUS},
            set(AWAITING_DELIVERY_ORDER_STATUSES),
            set(RETURN_ORDER_STATUSES),
            set(RETURN_RISK_ORDER_STATUSES),
        ]
        union = set().union(*buckets)
        # Đơn hủy (6) bị loại khỏi tổng nên KHÔNG thuộc rổ nào.
        self.assertEqual(union, {s.value for s in OrderStatus} - {OrderStatus.CANCELLED.value})
        self.assertEqual(sum(len(b) for b in buckets), len(union), "hai rổ chồng nhau")

    def test_awaiting_bucket_excludes_redelivery(self):
        from apps.orders._repositories_reports import (
            AWAITING_DELIVERY_ORDER_STATUSES,
            RETURN_RISK_ORDER_STATUSES,
        )

        # "Chờ giao lại" (10) là rủi ro hoàn, KHÔNG phải chờ giao — khác với
        # IN_DELIVERY_ORDER_STATUSES của nhóm 5a vốn gộp chung.
        self.assertEqual(RETURN_RISK_ORDER_STATUSES, (10,))
        self.assertNotIn(10, AWAITING_DELIVERY_ORDER_STATUSES)
        self.assertEqual(AWAITING_DELIVERY_ORDER_STATUSES, (0, 1, 2, 7, 8, 9, 11))


class OverviewTopProductsTests(TestCase):
    """Top sản phẩm — cơ sở DOANH THU THUẦN cộng theo DÒNG HÀNG (user chốt 2026-09-03).

    Tập đơn là ``net_revenue_q`` (đã lên hóa đơn, trừ hủy, trừ hoàn) — cùng tập
    với 2 khối địa lý và biểu đồ diễn biến. Tiền cộng theo ``OrderItem.line_total``
    nên đơn mua kèm chia đúng phần của từng SP; số ĐƠN đếm theo đơn chứa SP.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-overview-top-products")
        self.channel = Channel.objects.create(id=960001, code="ov-tp", name="OV Top Products")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000005", phone_normalized="0900000005"
        )
        self.pa = Product.objects.create(name="Ghế massage A", sku="SKU-A", price=Decimal("100"))
        self.pb = Product.objects.create(name="Đệm B", sku="SKU-B", price=Decimal("50"))
        self.pc = Product.objects.create(name="Gối C", sku="SKU-C", price=Decimal("10"))

    def _order(self, *, lines, status=3, invoice_status=3, source="landing"):
        """``lines``: (product, quantity, line_total)."""
        total = sum(Decimal(amount) for _, _, amount in lines)
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000005",
            status=status,
            invoice_status=invoice_status,
            order_source=source,
            province="Hà Nội",
            total_after_discount=Decimal(total),
        )
        for product, quantity, amount in lines:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=quantity,
                unit_price=Decimal(amount) / quantity,
                line_total=Decimal(amount),
            )
        return order

    def _rows(self, query=""):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/reports/overview{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()["top_products"]

    def test_ranks_by_net_line_revenue_with_distinct_order_count(self):
        # Đơn 1 giao TC: A 300k (1 cái) + B 200k (2 cái). Đơn 2 chờ giao: B 150k.
        self._order(lines=[(self.pa, 1, "300000"), (self.pb, 2, "200000")], status=3)
        self._order(lines=[(self.pb, 1, "150000")], status=1)

        rows = self._rows()
        self.assertEqual([r["sku"] for r in rows], ["SKU-B", "SKU-A"])
        b, a = rows
        self.assertEqual(b["product_id"], self.pb.id)
        self.assertEqual(b["name"], "Đệm B")
        self.assertEqual(b["revenue"], "350000.00")
        self.assertEqual(b["quantity"], 3)
        self.assertEqual(b["orders"], 2)
        # Chỉ đơn 1 giao thành công → phần của B trong đơn đó.
        self.assertEqual(b["delivered_orders"], 1)
        self.assertEqual(b["delivered_revenue"], "200000.00")
        self.assertEqual(a["revenue"], "300000.00")
        self.assertEqual(a["delivered_revenue"], "300000.00")

    def test_returned_cancelled_and_lead_orders_are_out_but_return_risk_stays(self):
        self._order(lines=[(self.pa, 1, "100000")], status=3)
        self._order(lines=[(self.pa, 1, "900000")], status=5)  # hoàn
        self._order(lines=[(self.pa, 1, "800000")], status=6)  # hủy
        self._order(lines=[(self.pa, 1, "700000")], invoice_status=None)  # lead
        self._order(lines=[(self.pa, 1, "25000")], status=10)  # rủi ro hoàn — vẫn tính

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["revenue"], "125000.00")
        self.assertEqual(rows[0]["orders"], 2)

    def test_product_filter_keeps_only_the_selected_products(self):
        self._order(lines=[(self.pa, 1, "300000"), (self.pb, 1, "200000")])
        self._order(lines=[(self.pc, 1, "50000")])

        rows = self._rows(f"?product_ids={self.pa.id},{self.pc.id}")
        self.assertEqual([r["sku"] for r in rows], ["SKU-A", "SKU-C"])

    def test_sale_channel_filter_applies(self):
        self._order(lines=[(self.pa, 1, "300000")], source="shopee")
        self._order(lines=[(self.pb, 1, "200000")], source="landing")

        rows = self._rows("?sale_channel=marketplace")
        self.assertEqual([r["sku"] for r in rows], ["SKU-A"])

    def test_empty_period_returns_empty_list(self):
        self.assertEqual(self._rows("?date_from=2020-01-01&date_to=2020-01-02"), [])
