"""Test API trang Chất lượng cuộc gọi: phân quyền + số liệu tổng hợp."""

from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.callcenter.models import (
    CallQualityReview,
    CallQualityScore,
    CallRecord,
    TranscribeStatus,
)
from apps.callcenter.qa_rubric import RUBRIC_VERSION
from apps.orders.models import OrderAssessment
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

SUMMARY_URL = "/api/callcenter/quality/summary"
TREND_URL = "/api/callcenter/quality/trend"
CALLS_URL = "/api/callcenter/quality/calls"
RUBRIC_URL = "/api/callcenter/quality/rubric"


def _make_score(
    *,
    call_id: str,
    total: float,
    call_date: date,
    criteria: dict | None = None,
    red_flags: list | None = None,
    transcript: str = "Nhân viên: alo ạ\nKhách: vâng",
) -> CallQualityScore:
    record = CallRecord.objects.create(
        call_id=call_id,
        transcribe_status=TranscribeStatus.DONE,
        transcript=transcript,
        recording_url="https://example.test/a.mp3",
        acd_time=300,
        start_time=timezone.make_aware(
            timezone.datetime(call_date.year, call_date.month, call_date.day, 10, 0)
        ),
    )
    return CallQualityScore.objects.create(
        call_record=record,
        rubric_version=RUBRIC_VERSION,
        call_date=call_date,
        duration_seconds=300,
        total_score=total,
        block_scores={"A": total / 4, "B": total / 4, "C": total / 4, "D": total / 4},
        criteria=criteria
        or {
            "a1_greeting": {"score": 2, "na": False, "evidence": "Nhân viên: alo ạ"},
            "b1_discovery": {"score": 0, "na": False, "evidence": ""},
            "d2_info_complete": {"score": None, "na": True, "evidence": ""},
        },
        red_flags=red_flags or [],
        summary="nhận xét",
    )


def _attach_status(score, code: str, *, staff_name: str = "") -> None:
    """Gắn đơn + phiếu đánh giá mang trạng thái sale cho một bản chấm.

    Bộ lọc và cột trạng thái đọc LIVE từ ``OrderAssessment`` (để khớp trang Đơn
    hàng), nên test phải dựng đúng đường đó — đặt ``sale_sales_status_code`` (bản
    chụp lúc chấm) không còn tác dụng với chúng nữa.
    """
    from apps.channels_core.models import Channel
    from apps.customers.models import Customer
    from apps.orders.models import Order, OrderAssessment

    channel, _ = Channel.objects.get_or_create(code="fb", defaults={"name": "Facebook"})
    customer = Customer.objects.create(full_name="KH", phone_raw="0900000000")
    staff = None
    if staff_name:
        staff = make_user_with_sections(
            f"sale-{score.call_record.call_id}", ["landing-orders"]
        )
        staff.display_name = staff_name
        staff.save(update_fields=["display_name"])
    order = Order.objects.create(channel=channel, customer=customer, staff=staff, name="KH")
    OrderAssessment.objects.create(order=order, sales_status_code=code)
    score.call_record.order = order
    score.call_record.save(update_fields=["order"])


@override_settings(QA_PASS_SCORE=80)
class CallQualityPermissionTests(TestCase):
    def test_khong_dang_nhap_thi_401(self):
        self.assertEqual(APIClient().get(SUMMARY_URL).status_code, 401)

    def test_thieu_section_call_quality_thi_403(self):
        user = make_user_with_sections("sale9", ["landing-orders"])
        client = APIClient()
        client.force_authenticate(user)

        for url in (SUMMARY_URL, TREND_URL, CALLS_URL, RUBRIC_URL):
            self.assertEqual(client.get(url).status_code, 403, url)

    def test_transcript_chi_tra_cho_nguoi_co_quyen(self):
        """Transcript là PII (SĐT, thông tin bệnh của khách) — nằm trọn sau quyền."""
        score = _make_score(call_id="perm-1", total=90, call_date=timezone.localdate())
        outsider = APIClient()
        outsider.force_authenticate(make_user_with_sections("kho9", ["stock"]))

        resp = outsider.get(f"/api/callcenter/quality/calls/{score.id}")

        self.assertEqual(resp.status_code, 403)


@override_settings(QA_PASS_SCORE=80)
class CallQualitySummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # User + role dựng MỘT lần cho cả class (rollback từng test về savepoint class).
        cls.user = make_user_with_sections("lead1", ["call-quality"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()

    def test_summary_dem_dung_va_tinh_ty_le_dat(self):
        _make_score(call_id="s-1", total=90, call_date=self.today)
        _make_score(call_id="s-2", total=70, call_date=self.today)

        resp = self.client.get(SUMMARY_URL)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_calls"], 2)
        self.assertEqual(data["avg_score"], 80.0)
        self.assertEqual(data["pass_rate"], 0.5)
        self.assertEqual(data["rubric_version"], RUBRIC_VERSION)

    def test_tieu_chi_sap_theo_diem_thap_dan_va_loai_na_khoi_mau_so(self):
        _make_score(call_id="s-crit", total=50, call_date=self.today)

        data = self.client.get(SUMMARY_URL).json()
        by_code = {item["code"]: item for item in data["criteria"]}

        # b1 bị 0 điểm phải đứng trước a1 được 2 điểm.
        codes = [item["code"] for item in data["criteria"] if item["call_count"] > 0]
        self.assertLess(codes.index("b1_discovery"), codes.index("a1_greeting"))
        # d2 là "na" ⇒ không vào mẫu số.
        self.assertEqual(by_code["d2_info_complete"]["call_count"], 0)
        self.assertEqual(by_code["b1_discovery"]["zero_count"], 1)

    def test_ngoai_khoang_ngay_thi_khong_tinh(self):
        _make_score(call_id="s-old", total=90, call_date=self.today - timedelta(days=90))

        data = self.client.get(SUMMARY_URL).json()

        self.assertEqual(data["total_calls"], 0)
        self.assertEqual(data["avg_score"], 0.0)

    def test_coverage_dem_ca_cuoc_chua_cham(self):
        _make_score(call_id="s-scored", total=90, call_date=self.today)
        # Cuộc có ghi âm nhưng chưa transcribe — vào mẫu số coverage, không vào điểm.
        CallRecord.objects.create(
            call_id="s-chua-transcribe",
            recording_url="https://example.test/b.mp3",
            start_time=timezone.now(),
        )

        data = self.client.get(SUMMARY_URL).json()

        self.assertEqual(data["coverage"]["total_calls"], 2)
        self.assertEqual(data["coverage"]["with_recording"], 2)
        self.assertEqual(data["coverage"]["transcribed"], 1)
        self.assertEqual(data["coverage"]["scored"], 1)

    def test_phan_loai_diem_manh_diem_yeu_va_hanh_dong(self):
        _make_score(
            call_id="s-insight",
            total=50,
            call_date=self.today,
            criteria={
                # 2.0/2 ⇒ điểm mạnh; 0.0/2 ⇒ điểm yếu; 1.2/2 rơi vào khoảng giữa,
                # cố ý không xếp vào đâu để danh sách không bị loãng.
                "a1_greeting": {"score": 2, "na": False, "evidence": "x"},
                "b1_discovery": {"score": 0, "na": False, "evidence": ""},
                "d2_info_complete": {"score": None, "na": True, "evidence": ""},
            },
        )

        data = self.client.get(SUMMARY_URL).json()

        self.assertIn("a1_greeting", [item["code"] for item in data["strengths"]])
        self.assertIn("b1_discovery", [item["code"] for item in data["weaknesses"]])
        # Tiêu chí "na" không có mẫu ⇒ không được coi là điểm yếu.
        self.assertNotIn("d2_info_complete", [item["code"] for item in data["weaknesses"]])
        actions = {item["code"]: item["action"] for item in data["actions"]}
        self.assertIn("b1_discovery", actions)
        self.assertTrue(actions["b1_discovery"])

    def test_khong_co_du_lieu_thi_khong_chia_cho_0(self):
        data = self.client.get(SUMMARY_URL).json()

        self.assertEqual(data["total_calls"], 0)
        self.assertEqual(data["pass_rate"], 0.0)


@override_settings(QA_PASS_SCORE=80)
class CallQualityCallsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("lead2", ["call-quality"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()

    def test_loc_theo_tieu_chi_bi_0_diem(self):
        _make_score(call_id="c-zero", total=50, call_date=self.today)
        _make_score(
            call_id="c-ok",
            total=95,
            call_date=self.today,
            criteria={"b1_discovery": {"score": 2, "na": False, "evidence": "x"}},
        )

        resp = self.client.get(CALLS_URL, {"criterion": "b1_discovery"})

        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["call_record_id"], CallRecord.objects.get(call_id="c-zero").id)

    def test_loc_theo_co_do(self):
        _make_score(
            call_id="c-flag",
            total=60,
            call_date=self.today,
            red_flags=[{"code": "rf_medical_claim", "evidence": "chữa khỏi hẳn"}],
        )
        _make_score(call_id="c-clean", total=90, call_date=self.today)

        resp = self.client.get(CALLS_URL, {"has_red_flag": "1"})

        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["red_flags"], ["rf_medical_claim"])

    def test_loc_theo_khoang_diem(self):
        _make_score(call_id="c-low", total=40, call_date=self.today)
        _make_score(call_id="c-high", total=95, call_date=self.today)

        items = self.client.get(CALLS_URL, {"min_score": 80}).json()["items"]

        self.assertEqual([item["total_score"] for item in items], [95.0])

    def test_chi_tiet_tra_trich_dan_va_transcript(self):
        score = _make_score(call_id="c-detail", total=85, call_date=self.today)

        data = self.client.get(f"/api/callcenter/quality/calls/{score.id}").json()

        self.assertIn("transcript", data)
        criteria = {item["code"]: item for item in data["criteria"]}
        self.assertEqual(criteria["a1_greeting"]["evidence"], "Nhân viên: alo ạ")
        self.assertTrue(criteria["d2_info_complete"]["na"])

    def test_chi_tiet_khong_ton_tai_thi_404(self):
        self.assertEqual(self.client.get("/api/callcenter/quality/calls/999999").status_code, 404)

    def test_tra_ten_sale_phu_trach_don_va_sdt_khach(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order

        staff = make_user_with_sections("sale-owner", ["landing-orders"])
        staff.display_name = "Nguyễn Thị A"
        staff.save(update_fields=["display_name"])
        channel = Channel.objects.create(code="fb", name="Facebook")
        customer = Customer.objects.create(full_name="Khách Test", phone_raw="0987000111")
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            staff=staff,
            name="Khách Test",
            phone="0987000111",
        )
        score = _make_score(call_id="c-sale", total=88, call_date=self.today)
        score.call_record.order = order
        score.call_record.caller = "0987000111"
        score.call_record.save(update_fields=["order", "caller"])

        row = self.client.get(CALLS_URL).json()["items"][0]

        self.assertEqual(row["sale_name"], "Nguyễn Thị A")
        self.assertEqual(row["customer_phone"], "0987000111")
        self.assertEqual(row["customer_name"], "Khách Test")

    def _order_with_sku(self, *, call_id: str, sku: str, staff_name: str):
        """Dựng 1 cuộc gọi đã chấm, khớp đơn có sale + 1 dòng hàng SKU."""
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order, OrderItem
        from apps.catalog.models import Product

        staff = make_user_with_sections(f"sale-{sku}", ["landing-orders"])
        staff.display_name = staff_name
        staff.save(update_fields=["display_name"])
        channel, _ = Channel.objects.get_or_create(code="fb", defaults={"name": "Facebook"})
        customer = Customer.objects.create(full_name="KH", phone_raw="0900000000")
        order = Order.objects.create(channel=channel, customer=customer, staff=staff, name="KH")
        product = Product.objects.create(name=f"SP {sku}", sku=sku)
        OrderItem.objects.create(order=order, product=product, sku=sku, quantity=1)

        score = _make_score(call_id=call_id, total=80, call_date=self.today)
        score.call_record.order = order
        score.call_record.save(update_fields=["order"])
        return staff, score

    def test_cot_sku_lay_tu_dong_hang_cua_don(self):
        self._order_with_sku(call_id="c-sku", sku="DLX-01", staff_name="Sale A")

        row = self.client.get(CALLS_URL).json()["items"][0]

        self.assertEqual(row["skus"], ["DLX-01"])

    def test_loc_theo_sale_phu_trach_don(self):
        staff_a, _ = self._order_with_sku(call_id="c-staff-a", sku="SKU-A", staff_name="Sale A")
        self._order_with_sku(call_id="c-staff-b", sku="SKU-B", staff_name="Sale B")

        items = self.client.get(CALLS_URL, {"staff": staff_a.id}).json()["items"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sale_name"], "Sale A")

    def test_loc_theo_sku(self):
        self._order_with_sku(call_id="c-sku-a", sku="SKU-A", staff_name="Sale A")
        self._order_with_sku(call_id="c-sku-b", sku="SKU-B", staff_name="Sale B")

        items = self.client.get(CALLS_URL, {"sku": "SKU-A"}).json()["items"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["skus"], ["SKU-A"])

    def test_endpoint_filters_chi_tra_gia_tri_co_that_trong_ky(self):
        _staff, score = self._order_with_sku(
            call_id="c-opt", sku="SKU-OPT", staff_name="Sale Opt"
        )
        OrderAssessment.objects.create(
            order=score.call_record.order, sales_status_code="need_more_consulting"
        )

        data = self.client.get("/api/callcenter/quality/filters").json()

        self.assertEqual([item["name"] for item in data["sales"]], ["Sale Opt"])
        self.assertEqual(data["skus"], ["SKU-OPT"])
        self.assertEqual(
            data["sales_statuses"],
            [{"code": "need_more_consulting", "label": "Tư vấn thêm"}],
        )

    def test_filters_bo_qua_trang_thai_rong(self):
        """Cuộc chưa khớp đơn có mã rỗng — chọn nó không lọc ra gì có nghĩa."""
        _make_score(call_id="c-opt-empty", total=70, call_date=self.today)

        data = self.client.get("/api/callcenter/quality/filters").json()

        self.assertEqual(data["sales_statuses"], [])

    def test_loc_theo_trang_thai_sale(self):
        keep = _make_score(call_id="c-status-keep", total=70, call_date=self.today)
        _attach_status(keep, "need_more_consulting")
        drop = _make_score(call_id="c-status-drop", total=90, call_date=self.today)
        _attach_status(drop, "created_order")

        items = self.client.get(CALLS_URL, {"sales_status": "need_more_consulting"}).json()[
            "items"
        ]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sales_status_label"], "Tư vấn thêm")

    def test_bang_theo_sale_tra_diem_tb_va_manh_yeu_cua_chinh_nguoi_do(self):
        staff, score = self._order_with_sku(
            call_id="c-agent", sku="SKU-AG", staff_name="Sale Agent"
        )
        score.criteria = {
            "a1_greeting": {"score": 2, "na": False, "evidence": "x"},
            "b1_discovery": {"score": 0, "na": False, "evidence": ""},
        }
        score.total_score = 60
        score.save(update_fields=["criteria", "total_score"])

        items = self.client.get("/api/callcenter/quality/agents").json()["items"]

        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["staff_id"], staff.id)
        self.assertEqual(row["name"], "Sale Agent")
        self.assertEqual(row["call_count"], 1)
        self.assertEqual(row["avg_score"], 60.0)
        self.assertEqual(row["best"]["code"], "a1_greeting")
        self.assertEqual(row["worst"]["code"], "b1_discovery")
        self.assertTrue(row["action"])

    def test_bang_theo_sale_bo_qua_cuoc_chua_khop_don(self):
        _make_score(call_id="c-agent-no-order", total=90, call_date=self.today)

        items = self.client.get("/api/callcenter/quality/agents").json()["items"]

        self.assertEqual(items, [])

    def test_bang_theo_sale_loc_duoc_theo_so_cuoc_toi_thieu(self):
        self._order_with_sku(call_id="c-agent-min", sku="SKU-MIN", staff_name="Sale Ít")

        items = self.client.get(
            "/api/callcenter/quality/agents", {"min_calls": 5}
        ).json()["items"]

        self.assertEqual(items, [])

    def test_tra_nhan_tieng_viet_cua_trang_thai_don(self):
        """Nhãn map từ crm_catalog ở BE — FE không tự chép danh sách mã."""
        score = _make_score(call_id="c-label", total=80, call_date=self.today)
        _attach_status(score, "need_more_consulting")

        row = self.client.get(CALLS_URL).json()["items"][0]

        self.assertEqual(row["sales_status_code"], "need_more_consulting")
        self.assertEqual(row["sales_status_label"], "Tư vấn thêm")

    def test_ma_la_khong_co_trong_catalog_thi_giu_nguyen_ma(self):
        score = _make_score(call_id="c-label-unknown", total=80, call_date=self.today)
        _attach_status(score, "ma_khong_ton_tai")

        row = self.client.get(CALLS_URL).json()["items"][0]

        self.assertEqual(row["sales_status_label"], "ma_khong_ton_tai")

    def test_cuoc_chua_khop_don_thi_ten_sale_rong_khong_vo(self):
        _make_score(call_id="c-no-order", total=70, call_date=self.today)

        row = self.client.get(CALLS_URL).json()["items"][0]

        self.assertEqual(row["sale_name"], "")
        self.assertEqual(row["customer_name"], "")


@override_settings(QA_PASS_SCORE=80)
class CallQualityReviewTests(TestCase):
    """Review gate bằng call-quality:edit (action thêm ở sweep N2 đợt 2;
    grandfather 0039 cấp :edit cho role có :view nên lead hiện hành vẫn chấm được).
    Fixture section đầy đủ = role prod sau grandfather."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("lead3", ["call-quality"])
        cls.score = _make_score(call_id="r-1", total=85, call_date=timezone.localdate())

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_view_only_khong_duoc_ghi_review(self):
        """Contract mới: call-quality:view trần chỉ ĐỌC; ghi review cần :edit —
        thu hồi :edit trên UI là siết được ngay."""
        client = APIClient()
        client.force_authenticate(
            make_user_with_permissions("cq-view-only", ["call-quality:view"])
        )
        resp = client.post(
            f"/api/callcenter/quality/calls/{self.score.id}/review",
            {"criterion": "b1_discovery", "human_score": 2},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(CallQualityReview.objects.filter(score=self.score).exists())

    def test_ghi_phan_hoi_khong_ghi_de_diem_ai(self):
        resp = self.client.post(
            f"/api/callcenter/quality/calls/{self.score.id}/review",
            {"criterion": "b1_discovery", "human_score": 2, "note": "sale có hỏi tuổi"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        review = CallQualityReview.objects.get(score=self.score)
        self.assertEqual(review.human_score, 2)
        self.assertEqual(review.ai_score, 0)  # chụp lại điểm AI tại thời điểm phản hồi
        self.score.refresh_from_db()
        self.assertEqual(self.score.criteria["b1_discovery"]["score"], 0)

    def test_tieu_chi_khong_hop_le_thi_400(self):
        resp = self.client.post(
            f"/api/callcenter/quality/calls/{self.score.id}/review",
            {"criterion": "khong_ton_tai"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_human_score_ngoai_khoang_thi_400(self):
        resp = self.client.post(
            f"/api/callcenter/quality/calls/{self.score.id}/review",
            {"human_score": 9},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)


class CallQualityRubricTests(TestCase):
    def test_tra_ve_rubric_hien_hanh(self):
        client = APIClient()
        client.force_authenticate(make_user_with_sections("lead4", ["call-quality"]))

        data = client.get(RUBRIC_URL).json()

        self.assertEqual(data["version"], RUBRIC_VERSION)
        self.assertEqual(len(data["blocks"]), 4)
        self.assertEqual(sum(block["weight"] for block in data["blocks"]), 100)


@override_settings(QA_PASS_SCORE=80)
class CallQualitySalesStatusFilterTests(TestCase):
    """Bộ lọc trạng thái sale áp cho CẢ TRANG, không riêng bảng danh sách.

    Đây là điểm khác v1: chọn "Tư vấn thêm" thì điểm TB, bảng tiêu chí, xu hướng
    và bảng theo sale đều phải nói về riêng nhóm hội thoại đó — nếu không thì
    không trả lời được câu "cuộc chốt được đơn khác gì cuộc khách suy nghĩ thêm".
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("qa-status", ["call-quality"])
        cls.today = timezone.localdate()

        cls.tu_van = _make_score(call_id="s-tuvan", total=60, call_date=cls.today)
        _attach_status(cls.tu_van, "need_more_consulting", staff_name="Sale Tư Vấn")
        cls.chot = _make_score(call_id="s-chot", total=90, call_date=cls.today)
        _attach_status(cls.chot, "created_order", staff_name="Sale Chốt")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_summary_chi_tinh_tren_nhom_da_chon(self):
        data = self.client.get(SUMMARY_URL, {"sales_status": "need_more_consulting"}).json()

        self.assertEqual(data["total_calls"], 1)
        self.assertEqual(data["avg_score"], 60.0)
        self.assertEqual(data["pass_rate"], 0.0)

    def test_trend_chi_tinh_tren_nhom_da_chon(self):
        items = self.client.get(TREND_URL, {"sales_status": "created_order"}).json()["items"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["call_count"], 1)
        self.assertEqual(items[0]["avg_score"], 90.0)

    def test_bang_theo_sale_chi_tinh_tren_nhom_da_chon(self):
        items = self.client.get(
            "/api/callcenter/quality/agents", {"sales_status": "created_order"}
        ).json()["items"]

        self.assertEqual([row["name"] for row in items], ["Sale Chốt"])

    def test_coverage_KHONG_theo_bo_loc_va_bao_ro_dieu_do(self):
        """Coverage là dòng đếm hạ tầng — lọc theo trạng thái sale là vô nghĩa."""
        data = self.client.get(SUMMARY_URL, {"sales_status": "created_order"}).json()

        self.assertEqual(data["total_calls"], 1)
        self.assertEqual(data["coverage"]["scored"], 2)
        self.assertTrue(data["coverage"]["ignores_filters"])

    def test_khong_loc_thi_coverage_khong_bat_co(self):
        data = self.client.get(SUMMARY_URL).json()

        self.assertEqual(data["total_calls"], 2)
        self.assertFalse(data["coverage"]["ignores_filters"])

    def test_o_sale_thu_hep_theo_trang_thai_nhung_o_trang_thai_thi_khong(self):
        data = self.client.get(
            "/api/callcenter/quality/filters", {"sales_status": "created_order"}
        ).json()

        self.assertEqual([item["name"] for item in data["sales"]], ["Sale Chốt"])
        # Danh sách trạng thái vẫn đủ cả hai — thu hẹp theo chính nó sẽ khoá
        # người dùng lại ở lựa chọn hiện tại, không đổi sang nhóm khác được.
        self.assertEqual(
            sorted(item["code"] for item in data["sales_statuses"]),
            ["created_order", "need_more_consulting"],
        )

    def test_chi_tiet_bao_khi_sale_da_sua_trang_thai_sau_khi_cham(self):
        self.tu_van.sale_sales_status_code = "created_order"
        self.tu_van.save(update_fields=["sale_sales_status_code"])

        data = self.client.get(f"{CALLS_URL}/{self.tu_van.id}").json()

        self.assertEqual(data["sales_status_code"], "need_more_consulting")
        self.assertEqual(data["cross_check"]["sale_sales_status_code"], "created_order")
        self.assertTrue(data["cross_check"]["sales_status_changed_since"])

    def test_khong_lech_thi_khong_bat_co_da_sua(self):
        self.chot.sale_sales_status_code = "created_order"
        self.chot.save(update_fields=["sale_sales_status_code"])

        data = self.client.get(f"{CALLS_URL}/{self.chot.id}").json()

        self.assertFalse(data["cross_check"]["sales_status_changed_since"])
