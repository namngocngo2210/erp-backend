"""rows.py thuần: dict SQL → dict JSON. Mọi cột NULL vẫn ra object con đủ khoá."""

from datetime import date, datetime, UTC
from decimal import Decimal

from django.test import SimpleTestCase

from apps.daily_report.application import rows
from apps.daily_report.infrastructure.queries import ORDER_COLUMNS


def _blank(**overrides) -> dict:
    base = dict.fromkeys(ORDER_COLUMNS)
    base.update(don_id=1, la_don_gtc=False)
    base.update(overrides)
    return base


class OrderRowTests(SimpleTestCase):
    def test_all_null_gives_stable_shape(self):
        out = rows.order_row(_blank())
        self.assertEqual(out["id"], 1)
        self.assertEqual(out["cursor"], 1)
        self.assertIsNone(out["staff"])
        self.assertIsNone(out["channel"])
        self.assertEqual(out["order_status"], {"code": None, "label": None, "bucket": None})
        self.assertEqual(out["sales_status"], {"code": None, "label": None, "bucket": None})
        self.assertEqual(out["items"], {"line_count": None, "skus": [], "skus_label": None, "main_sku": None})
        self.assertEqual(set(out["customer"]), {"id", "code", "name", "phone_masked", "province", "district", "is_new"})
        self.assertEqual(set(out["amounts"]), {"goods", "discount", "shipping", "total", "quantity"})
        self.assertEqual(set(out["calls"]), {"count", "seconds", "has_transcript", "ai_extracted", "quality_score"})
        self.assertFalse(out["is_valid_revenue"])

    def test_values_mapped(self):
        out = rows.order_row(_blank(
            ma_don="DL1", ngay_tao=datetime(2026, 9, 15, 3, 0, tzinfo=UTC), ngay_tao_ngay=date(2026, 9, 15),
            trang_thai_don_ma=3, trang_thai_don_text="Giao thành công",
            trang_thai_sale_ma="closed", trang_thai_sale_text="Đã chốt",
            kenh_id=12, kenh_ten="FB Cường", loai_kenh_text="Facebook", nhom_kenh_ban="facebook",
            nhan_vien_id=45, nhan_vien_ten="A", sdt_che="0912***678",
            tong_sau_giam=Decimal("1430000.4"), danh_sach_sku=["MSC06", "GEL01"], diem_chat_luong=Decimal("7.5"),
        ))
        self.assertEqual(out["code"], "DL1")
        self.assertEqual(out["created_at"], "2026-09-15T10:00:00+07:00")
        self.assertEqual(out["created_date"], "2026-09-15")
        self.assertEqual(out["order_status"], {"code": 3, "label": "Giao thành công", "bucket": "delivered"})
        self.assertEqual(out["sales_status"]["bucket"], "closed")
        self.assertEqual(out["channel"], {"id": 12, "name": "FB Cường", "type": "Facebook"})
        self.assertEqual(out["channel_group"], "facebook")
        self.assertEqual(out["staff"], {"id": 45, "name": "A"})
        self.assertEqual(out["customer"]["phone_masked"], "0912***678")
        self.assertEqual(out["amounts"]["total"], 1430000)
        self.assertIsInstance(out["amounts"]["total"], int)
        self.assertEqual(out["items"]["skus"], ["MSC06", "GEL01"])
        self.assertEqual(out["calls"]["quality_score"], 7.5)

    def test_fb_attribution_on_order_and_open_lead_rows(self):
        tagged = _blank(chien_dich_fb_id="120249877608640636", quang_cao_fb_id="120249877608630636")
        self.assertEqual(rows.order_row(tagged)["fb_campaign_id"], "120249877608640636")
        self.assertEqual(rows.order_row(tagged)["fb_ad_id"], "120249877608630636")
        self.assertEqual(rows.open_lead_row({**tagged, "tuoi": 1})["fb_campaign_id"], "120249877608640636")
        self.assertIsNone(rows.order_row(_blank())["fb_campaign_id"])
        self.assertIsNone(rows.order_row(_blank())["fb_ad_id"])

    def test_open_lead_row_is_subset_with_age(self):
        out = rows.open_lead_row({**_blank(nhan_vien_id=1, nhan_vien_ten="A"), "tuoi": 6})
        self.assertEqual(out["age_days"], 6)
        self.assertEqual(out["staff"], {"id": 1, "name": "A"})
        self.assertNotIn("order_status", out)
        self.assertNotIn("invoice_status", out)
        self.assertEqual(out["cursor"], 1)

    def test_order_line_row(self):
        out = rows.order_line_row({
            "dong_id": 9, "don_id": 1, "ma_don": "DL1", "ngay_tao_ngay": date(2026, 9, 15),
            "sku": "MSC06", "ten_sp": "Ghế", "nhom_sp_text": None, "so_luong": 2,
            "don_gia": Decimal("700000"), "thanh_tien": Decimal("1400000"),
        })
        self.assertEqual(out, {
            "line_id": 9, "order_id": 1, "order_code": "DL1", "created_date": "2026-09-15",
            "sku": "MSC06", "product_name": "Ghế", "product_group": None, "quantity": 2,
            "unit_price": 700000, "amount": 1400000, "cursor": 9,
        })
