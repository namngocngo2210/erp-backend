"""Kiểm bản dịch: dựng đơn thật qua ORM, full_rebuild, soi từng cột ra nhãn đúng.

Dùng chung fixture đơn của apps/ai_mart/tests/helpers.py (mk_order) — bám codebase,
factory OrderFactory không tồn tại trong repo này.
"""

from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.don_hang import MART
from apps.ai_mart.tests.helpers import mk_order
from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.orders.models import Order, OrderAssessment, OrderItem


def _row(order_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT trang_thai_don_ma, trang_thai_don_text, trang_thai_sale_text,"
            " sdt_che, ngay_tao_ngay, la_don_gtc, tuoi_text, tuoi_nguon_text"
            " FROM ai.don_hang WHERE don_id = %s",
            [order_id],
        )
        cols = [c[0] for c in cur.description]
        r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


class DonHangTransformTests(TestCase):
    def test_status_and_sales_status_translated(self):
        order = mk_order(status=3)
        OrderAssessment.objects.update_or_create(
            order=order, defaults={"sales_status_code": "no_answer_5", "age_text": "45_54"}
        )
        loaders.full_rebuild(MART)
        row = _row(order.id)
        self.assertEqual(row["trang_thai_don_ma"], 3)
        self.assertEqual(row["trang_thai_don_text"], "Giao thành công")
        self.assertEqual(row["trang_thai_sale_text"], "Không nghe máy lần 5")
        self.assertEqual(row["tuoi_text"], "45-54")
        self.assertEqual(row["tuoi_nguon_text"], "Sale nhập")

    def test_blank_sales_status_becomes_null(self):
        order = mk_order(status=0)
        OrderAssessment.objects.update_or_create(order=order, defaults={"sales_status_code": ""})
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT trang_thai_sale_ma, trang_thai_sale_text FROM ai.don_hang WHERE don_id = %s",
                [order.id],
            )
            trang_thai_sale_ma, trang_thai_sale_text = cur.fetchone()
        self.assertIsNone(trang_thai_sale_ma)
        self.assertIsNone(trang_thai_sale_text)

    def test_empty_age_becomes_null(self):
        order = mk_order(status=0)
        OrderAssessment.objects.update_or_create(order=order, defaults={"age_text": "   "})
        loaders.full_rebuild(MART)
        row = _row(order.id)
        self.assertIsNone(row["tuoi_text"])
        self.assertIsNone(row["tuoi_nguon_text"])

    def test_mirror_and_soft_deleted_excluded(self):
        kept = mk_order(status=0)
        mirror = mk_order(status=0, order_source="foxia")
        gone = mk_order(status=0)
        Order.objects.filter(id=gone.id).update(deleted_at="2026-01-01T00:00:00Z")
        loaders.full_rebuild(MART)
        self.assertIsNotNone(_row(kept.id))
        self.assertIsNone(_row(mirror.id))
        self.assertIsNone(_row(gone.id))

    def test_blank_order_code_and_sku_become_null(self):
        order = mk_order(status=0)  # mk_order không set order_code -> mặc định ''
        product = Product.objects.create(name="Test Product", sku="TEST-SKU-BLANK-CASE")
        OrderItem.objects.create(order=order, product=product, sku="")
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT ma_don, danh_sach_sku, sku_chinh FROM ai.don_hang WHERE don_id = %s",
                [order.id],
            )
            ma_don, danh_sach_sku, sku_chinh = cur.fetchone()
        self.assertIsNone(ma_don)
        self.assertEqual(danh_sach_sku, [])
        self.assertIsNone(sku_chinh)

    def test_chi_nhanh_and_nhom_kenh_ban(self):
        """Nhóm kênh bán chép luật Báo cáo tổng: sàn xét TRƯỚC kênh."""
        fb = Channel.objects.create(code="facebook-page-x", name="FB X")
        web = Channel.objects.create(code="google-ads-web", name="Web")
        o_fb = mk_order(status=0, channel=fb, branch_id="231174")
        o_shopee_on_fb = mk_order(status=0, channel=fb, invoice_code="HDSPE_000001")
        o_tiktok = mk_order(status=0, order_source="tiktok")
        o_web = mk_order(status=0, channel=web)
        o_other = mk_order(status=0)  # kênh test-ch-N, không nguồn, không mã HĐ
        loaders.full_rebuild(MART)
        got = {}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT don_id, chi_nhanh_id, nhom_kenh_ban FROM ai.don_hang WHERE don_id = ANY(%s)",
                [[o_fb.id, o_shopee_on_fb.id, o_tiktok.id, o_web.id, o_other.id]],
            )
            for don_id, chi_nhanh, nhom in cur.fetchall():
                got[don_id] = (chi_nhanh, nhom)
        self.assertEqual(got[o_fb.id], ("231174", "facebook"))
        self.assertEqual(got[o_shopee_on_fb.id], (None, "shopee"))
        self.assertEqual(got[o_tiktok.id], (None, "tiktok"))
        self.assertEqual(got[o_web.id], (None, "web"))
        self.assertEqual(got[o_other.id], (None, "off_other"))

    def test_fb_campaign_and_ad_id_columns(self):
        """Id chiến dịch/quảng cáo Facebook chép từ orders; rỗng → NULL."""
        tagged = mk_order(status=0, fb_campaign_id="120249877608640636", fb_ad_id="120249877608630636")
        untagged = mk_order(status=0)
        loaders.full_rebuild(MART)
        got = {}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT don_id, chien_dich_fb_id, quang_cao_fb_id FROM ai.don_hang WHERE don_id = ANY(%s)",
                [[tagged.id, untagged.id]],
            )
            for don_id, camp, ad in cur.fetchall():
                got[don_id] = (camp, ad)
        self.assertEqual(got[tagged.id], ("120249877608640636", "120249877608630636"))
        self.assertEqual(got[untagged.id], (None, None))

    def test_original_with_live_restored_clone_excluded(self):
        """Cùng luật overview.py: đơn gốc bị huỷ mà có bản khôi phục còn sống thì
        không vào mart — nếu không, gốc + bản sao cùng rơi vào một kỳ báo cáo."""
        original = mk_order(status=6)
        clone = mk_order(status=0, restored_from=original)
        loaders.full_rebuild(MART)
        self.assertIsNone(_row(original.id))
        self.assertIsNotNone(_row(clone.id))


class DonHangIncrementalUpsertTests(TransactionTestCase):
    """TransactionTestCase là ngoại lệ TỐN CHI PHÍ nhưng bắt buộc ở đây: trong
    Django TestCase (atomic wrapper), Postgres now() cố định theo mốc bắt đầu
    transaction nên INSERT (mk_order) và UPDATE sau cutoff nhận CÙNG một
    dw_updated_at — candidate_ids sẽ luôn rỗng. Cùng bẫy đã ghi ở test_triggers.py.
    """

    def test_incremental_upsert_reflects_status_change_via_queryset_update(self):
        order = mk_order(status=2)
        loaders.full_rebuild(MART)
        from django.utils import timezone

        cutoff = timezone.now()
        Order.objects.filter(id=order.id).update(status=3)  # đường webhook KiotViet
        ids = loaders.candidate_ids(MART, cutoff)
        self.assertIn(order.id, ids)
        loaders.upsert_by_ids(MART, ids)
        self.assertEqual(_row(order.id)["trang_thai_don_text"], "Giao thành công")
